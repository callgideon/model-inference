-- D2: atomic admission (research/plan/handoffs/D-durable-state.md §D2; 02 §1-§2;
-- 08 §10 R1, R4, R6, R10, R20, R29/R79, R45, R53, R55, R66, R69, R70, R78).
--
-- Fills the body of D1's `infrx.admit(jsonb)` boundary (0004) - signature and grants
-- unchanged - and adds the helpers it runs. ONE transaction per call:
--
--   idempotency compare / replay / 409 / 410  ->  R6 (a request UUID is admitted once)
--   ->  authorization recheck (key revoked, org suspended, entitlement)  ->  R29 clamp on
--   the store clock  ->  R55 token ceilings  ->  capacity (total, org, key, preparation
--   R1, journal bytes)  ->  price/pins from the STORE (R45/R69)  ->  the store-derived
--   maximum hold (R53, ceiling_8)  ->  available balance  ->  job + hold + three capacity
--   reservations + prepare_dispatch outbox + idempotency mapping.
--
-- Everything that can refuse runs before anything is written, and a refusal raises, so
-- the transaction leaves nothing behind (DUR-ADMIT "rejects own none").
--
-- LOCK ORDER (DUR-CAP; every D writer that touches these must take them in this order):
--   1. the admission capacity scope: `pg_advisory_xact_lock(infrx.admission_lock_key())`.
--      One scope, because the `total` active-job cap is platform-wide - counting it is a
--      global question. ponytail: one global admission lock; per-scope counters with
--      their own row locks if admission throughput ever matters.
--   2. the idempotency scope row (org, operation, key)             FOR UPDATE
--   3. the organization row                                        FOR SHARE
--   4. the API key row                                             FOR SHARE
--   5. the wallet row (`infrx.wallets` / `infrx.credit_wallets`)   FOR UPDATE
-- A grant takes only 5 (the ledger trigger); settlement (D5) must take 5 without 1.
-- Nothing takes a lower-numbered lock while holding a higher one, so no cycle exists.
--
-- Refusals are SQLSTATE P0001 with the message `<error_code>: <detail>` (the codes of
-- `contracts/errors.py`) and, for 429s, the hint `retry_after=<s>`; the adapter
-- (`infrx/state/jobstore.py`) maps the code to its `DomainError`. D1R's seams keep their
-- own SQLSTATEs (55000 maintenance, P0002 not_found, 22023 invalid_request, 23514 with
-- `credit_wallets_reserved_within_total` = insufficient credit).
--
-- Units never meet (R64/R65, `check_no_unit_conversion`): the legacy USD body and the
-- CREDIT body are separate functions, and the shared helpers read neither money table.
--
-- Additive and re-runnable.

-- ======================================================== job columns (additive) ===
-- The canonical request (so `load_work` needs no second store), the idempotency payload
-- hash the admission answered with, and the prepared refs `prepared` stores once.
alter table infrx.jobs
  add column if not exists request_record jsonb,
  add column if not exists idem_payload_hash text,
  add column if not exists prepared_refs jsonb;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.jobs'::regclass
                 and conname = 'jobs_request_record_bounded') then
    alter table infrx.jobs
      add constraint jobs_request_record_bounded
        check (request_record is null
               or (jsonb_typeof(request_record) = 'object'
                   and octet_length(request_record::text) <= 1048576)),
      add constraint jobs_idem_payload_hash_shape
        check (idem_payload_hash is null or idem_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
      add constraint jobs_prepared_refs_shape
        check (prepared_refs is null or (jsonb_typeof(prepared_refs) = 'array'
                                         and octet_length(prepared_refs::text) <= 262144));
  end if;
end $$;

-- The admitted request is as immutable as its price (R53/R78); prepared refs are written
-- once, by the preparing -> queued transition.
create or replace function infrx.jobs_admission_record_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if new.request_record is distinct from old.request_record
     or new.idem_payload_hash is distinct from old.idem_payload_hash
     or (old.prepared_refs is not null
         and new.prepared_refs is distinct from old.prepared_refs) then
    raise exception 'job %: the admitted request and its prepared refs are immutable',
      old.request_id using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger jobs_admission_record_guard before update on infrx.jobs
  for each row execute function infrx.jobs_admission_record_guard();

-- Journal accounting reads the jobs still holding stored bytes (D4 prunes them).
create index if not exists jobs_journal_stored_idx on infrx.jobs (request_id)
  where journal_stored_bytes > 0;

-- ================================================================== helpers ===
create or replace function infrx.refuse(p_code text, p_detail text,
                                        p_retry_after_s int default null)
returns void language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  raise exception using errcode = 'P0001', message = p_code || ': ' || p_detail,
    hint = coalesce('retry_after=' || p_retry_after_s, '');
end $$;

create or replace function infrx.admission_lock_key() returns bigint
language sql immutable set search_path = infrx, public, pg_temp as $$
  select 7218390452001::bigint;       -- 'infrx admission capacity scope', fixed forever
$$;

-- The maximum hold (R53, §4 `ceiling_8`): (in x rate_in + out x rate_out) / 10^6, rounded
-- UP to 1e-8. Multiplying by 100 and dividing by 10^8 keeps every step exact in numeric.
create or replace function infrx.hold_for(p_in int, p_out int, p_in_rate numeric,
                                          p_out_rate numeric)
returns numeric language sql immutable set search_path = infrx, public, pg_temp as $$
  select (ceil((p_in::numeric * p_in_rate + p_out::numeric * p_out_rate) * 100)
          * 0.00000001)::numeric(20,8);
$$;

-- Journal bytes charged now: a job counts its live reservation or its stored bytes,
-- whichever is larger - never both (the fake's `_Journal.charge`).
create or replace function infrx.journal_bytes_charged() returns bigint
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(sum(greatest(coalesce(r.amount, 0), j.journal_stored_bytes)), 0)::bigint
  from infrx.jobs j
  left join infrx.capacity_reservations r
    on r.request_id = j.request_id and r.kind = 'journal_bytes' and r.active
  where r.request_id is not null or j.journal_stored_bytes > 0;
$$;

-- Entitlement (R24): no row or NULL = the platform default set (every model), '{}' =
-- nothing, a list = exactly those. A model matches by its revision string or its alias.
create or replace function infrx.is_entitled(p_org uuid, p_model text) returns boolean
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce((select e.model_ids is null
                          or p_model = any(e.model_ids)
                          or split_part(p_model, '@', 1) = any(e.model_ids)
                   from infrx.org_entitlements e where e.org_id = p_org), true);
$$;

-- The admission as the port returns it, for a new admission, a replay and `get_owned`.
-- Money is text; the CREDIT pins and card ride along for a CREDIT job, the terminal
-- facts once the job is terminal.
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
        'reserved_at', r.reserved_at) order by r.kind)
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
    'queue_deadline_at', j.queue_deadline_at, 'queue_wait_used_s', j.queue_wait_used_s,
    'accounting_regime', j.accounting_regime,
    'wallet_id', j.wallet_id,
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
        'result_ref', j.result_ref, 'settlement_state', j.settlement_state,
        'debit', j.debit::text, 'settled_at', j.settled_at,
        'reconcile_after', j.reconcile_after) end,
    'replayed', false)
  from infrx.jobs j where j.request_id = p_request_id;
$$;

-- Steps 1-2: the capacity scope lock, then the idempotency scope. Answers the original
-- admission for a matching replay, raises 409/410 for a changed payload or an expired
-- tombstone, NULL when there is nothing to replay. The tombstone clock starts at the
-- terminal state (01), so an active job's mapping never expires.
create or replace function infrx.admission_replay(p_idem jsonb, p_ttl_s double precision,
                                                  p_now timestamptz)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  i infrx.idempotency%rowtype;
  v_expires timestamptz;
begin
  perform pg_advisory_xact_lock(infrx.admission_lock_key());
  if p_idem->>'key' is null then
    return null;
  end if;
  select * into i from infrx.idempotency
   where org_id = (p_idem->>'org_id')::uuid and operation = p_idem->>'operation'
     and key = p_idem->>'key'
   for update;
  if not found then
    return null;
  end if;
  select coalesce(i.expires_at, j.settled_at + make_interval(secs => p_ttl_s))
    into v_expires from infrx.jobs j where j.request_id = i.request_id;
  if v_expires is not null and p_now >= v_expires then
    perform infrx.refuse('idempotency_expired',
                         'idempotency key expired at ' || v_expires::text);
  end if;
  if i.payload_digest <> p_idem->>'payload_hash' then
    perform infrx.refuse('idempotency_conflict',
                         'same idempotency key, different canonical payload');
  end if;
  return infrx.job_admission(i.request_id) || '{"replayed": true}';
end $$;

-- Steps 3-4 and the checks that need no price: tenant coherence, the authorization
-- recheck (R10), the R29 clamp on the store clock, the R55 ceilings and capacity (R1).
-- Returns the deadline the job keeps.
create or replace function infrx.admission_checks(p_request jsonb, p_limits jsonb,
                                                  p_budgets jsonb, p_model text,
                                                  p_now timestamptz)
returns timestamptz language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_org uuid := (p_request->>'org_id')::uuid;
  v_key uuid := (p_request->>'key_id')::uuid;
  v_in int := (p_request->>'max_input_tokens')::int;
  v_out int := (p_request->>'max_output_tokens')::int;
  v_deadline timestamptz := (p_request->>'deadline_at')::timestamptz;
  v_ceiling timestamptz;
  v_suspended boolean;
  v_count int;
  v_reserve bigint := (p_limits->>'journal_job_reserve_bytes')::bigint;
begin
  -- R55 (tenant): every media ref is the request's own organization's.
  if exists (select 1 from jsonb_array_elements(coalesce(p_request->'media', '[]')) m
             where (m->>'org_id')::uuid is distinct from v_org) then
    perform infrx.refuse('not_found', 'a request may only carry its own org''s media');
  end if;
  -- R10: a cached identity never bypasses revocation, suspension or entitlement.
  select o.suspended into v_suspended from public.organizations o where o.id = v_org
    for share;
  if not found then
    perform infrx.refuse('invalid_api_key', 'no such organization');
  end if;
  perform 1 from public.api_keys k
   where k.id = v_key and k.org_id = v_org and k.revoked_at is null
   for share;
  if not found then
    perform infrx.refuse('invalid_api_key', 'key ' || v_key || ' is revoked or unknown');
  end if;
  if v_suspended then
    perform infrx.refuse('org_suspended', 'org ' || v_org || ' is suspended');
  end if;
  if not infrx.is_entitled(v_org, p_model) then
    perform infrx.refuse('model_not_entitled',
                         'org ' || v_org || ' is not entitled to ' || p_model);
  end if;
  -- R29/R79: an elapsed deadline is refused; a later one is clamped to what the store can
  -- keep, measured on the store's clock and its own budgets snapshot.
  if v_deadline is null or v_deadline <= p_now then
    perform infrx.refuse('invalid_request', 'the request deadline has already passed');
  end if;
  v_ceiling := p_now + make_interval(secs => (p_budgets->>'preparation_s')::float8
                                         + (p_budgets->>'queue_wait_s')::float8
                                         + (p_budgets->>'generation_s')::float8);
  v_deadline := least(v_deadline, v_ceiling);
  -- R55: the ceilings are the store's to range-check, inside the transaction.
  if v_out is null or v_out < 1 or v_out > (p_limits->>'max_output_tokens')::int then
    perform infrx.refuse('invalid_request', 'max_output_tokens must be in 1..'
                         || (p_limits->>'max_output_tokens'));
  end if;
  if v_in is null or v_in < 1 then
    perform infrx.refuse('invalid_request', 'max_input_tokens must be at least 1');
  end if;
  if v_in + v_out > (p_limits->>'max_context_tokens')::int then
    perform infrx.refuse('context_length_exceeded',
                         'max_input_tokens + max_output_tokens exceeds MAX_CONTEXT_TOKENS');
  end if;
  -- DUR-CAP: each scope on its own (the counts are exact under lock 1).
  select count(*) into v_count from infrx.jobs where state in ('preparing','queued','running');
  if v_count >= (p_limits->>'max_active_jobs')::int then
    perform infrx.refuse('capacity_exhausted', 'total active job limit reached', 5);
  end if;
  select count(*) into v_count from infrx.jobs
   where org_id = v_org and state in ('preparing','queued','running');
  if v_count >= (p_limits->>'max_active_jobs_per_org')::int then
    perform infrx.refuse('capacity_exhausted', 'org active job limit reached', 5);
  end if;
  select count(*) into v_count from infrx.jobs
   where key_id = v_key and state in ('preparing','queued','running');
  if v_count >= (p_limits->>'max_active_jobs_per_key')::int then
    perform infrx.refuse('capacity_exhausted', 'key active job limit reached', 5);
  end if;
  select count(*) into v_count from infrx.capacity_reservations
   where kind = 'preparation' and active;
  if v_count >= (p_limits->>'max_preparing_jobs')::int then
    perform infrx.refuse('capacity_exhausted', 'preparation capacity reached', 5);
  end if;
  if infrx.journal_bytes_charged() + v_reserve > (p_limits->>'journal_total_bytes')::bigint then
    perform infrx.refuse('journal_capacity_exhausted',
                         'the journal budget cannot fit another reservation', 30);
  end if;
  return v_deadline;
end $$;

-- The rows every accepted admission owns besides its job and hold: three capacity
-- reservations, the prepare_dispatch outbox event and the idempotency mapping.
create or replace function infrx.admission_rows(p_request jsonb, p_idem jsonb,
                                                p_limits jsonb, p_handle text,
                                                p_now timestamptz)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_id uuid := (p_request->>'request_id')::uuid;
  v_org uuid := (p_request->>'org_id')::uuid;
  v_key uuid := (p_request->>'key_id')::uuid;
begin
  insert into infrx.capacity_reservations (request_id, kind, org_id, key_id, amount,
                                           reserved_at)
  values (v_id, 'preparation', v_org, v_key, 1, p_now),
         (v_id, 'inference', v_org, v_key, 1, p_now),
         (v_id, 'journal_bytes', v_org, v_key,
          (p_limits->>'journal_job_reserve_bytes')::bigint, p_now);
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), v_id, v_org, 'prepare_dispatch',
          jsonb_build_object('job_handle', p_handle, 'request_id', v_id), p_now);
  if p_idem->>'key' is not null then
    insert into infrx.idempotency (org_id, operation, key, payload_digest, request_id)
    values (v_org, p_idem->>'operation', p_idem->>'key', p_idem->>'payload_hash', v_id);
  end if;
end $$;

-- The job row, whatever its regime: the request's columns, the regime's provenance
-- (`p_regime` plus either the USD price version/snapshot or the CREDIT pins as a jsonb
-- object) and the store-derived hold. One explicit column list, so a column a later
-- migration adds takes its own default.
create or replace function infrx.admission_insert_job(p_request jsonb, p_idem jsonb,
  p_budgets jsonb, p_limits jsonb, p_deadline timestamptz, p_now timestamptz,
  p_regime text, p_model_revision text, p_price_version text, p_price_snapshot jsonb,
  p_pins jsonb, p_hold numeric)
returns infrx.jobs language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs;
begin
  insert into infrx.jobs (request_id, job_handle, org_id, key_id, model_revision,
    execution_mode, state, operation, idempotency_key, idem_payload_hash, payload_ref,
    payload_digest, max_input_tokens, max_output_tokens, price_version, price_snapshot,
    maximum_hold, consent_version, trace_mode, admitted_at, deadline_at,
    budget_preparation_s, budget_queue_wait_s, budget_generation_s, budget_first_token_s,
    budget_stall_s, preparation_deadline_at, journal_reserved_bytes, request_record,
    accounting_regime, wallet_id, model_id, requested_model, deployment_revision_id,
    serving_version_id, rate_card_version, policy_version, created_at, updated_at)
  values ((p_request->>'request_id')::uuid,
    'job_' || replace(gen_random_uuid()::text, '-', ''),
    (p_request->>'org_id')::uuid, (p_request->>'key_id')::uuid, p_model_revision,
    p_request->>'execution_mode', 'preparing', p_idem->>'operation', p_idem->>'key',
    p_idem->>'payload_hash', p_request->>'payload_ref', p_request->>'payload_digest',
    (p_request->>'max_input_tokens')::int, (p_request->>'max_output_tokens')::int,
    p_price_version, p_price_snapshot, p_hold,
    (p_request->'trace_policy'->>'consent_version')::int,
    p_request->'trace_policy'->>'trace_mode', p_now, p_deadline,
    (p_budgets->>'preparation_s')::float8, (p_budgets->>'queue_wait_s')::float8,
    (p_budgets->>'generation_s')::float8, (p_budgets->>'first_token_s')::float8,
    (p_budgets->>'stall_s')::float8,
    -- R20: the preparation phase starts now and never outlives the job.
    least(p_now + make_interval(secs => (p_budgets->>'preparation_s')::float8), p_deadline),
    (p_limits->>'journal_job_reserve_bytes')::bigint, p_request, p_regime,
    (p_pins->>'wallet_id')::uuid, (p_pins->>'model_id')::uuid, p_pins->>'requested_model',
    (p_pins->>'deployment_revision_id')::uuid, (p_pins->>'serving_version_id')::uuid,
    p_pins->>'rate_card_version', p_pins->>'policy_version', p_now, p_now)
  returning * into j;
  return j;
end $$;

-- ====================================================== legacy USD admission ===
-- The v1 port: price from `infrx.price_versions` (R45), hold in `infrx.credit_holds`,
-- reservation on `infrx.wallets`.
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
  p infrx.price_versions%rowtype;
  v_hold numeric(20,8);
  v_available numeric(20,8);
  j infrx.jobs;
begin
  perform infrx.require_feature('legacy_usd_admission');
  v_replay := infrx.admission_replay(v_idem, (v_limits->>'idempotency_ttl_s')::float8, v_now);
  if v_replay is not null then
    return v_replay;
  end if;
  if exists (select 1 from infrx.jobs where request_id = (r->>'request_id')::uuid) then
    perform infrx.refuse('state_conflict', 'request ' || (r->>'request_id')
                         || ' is already an admitted job');
  end if;
  v_deadline := infrx.admission_checks(r, v_limits, v_budgets, r->>'model_revision', v_now);
  -- R45: the price source is the store's; the request's parameters are never read.
  select * into p from infrx.price_versions pv
   where pv.model_revision = r->>'model_revision' and pv.effective_from <= v_now
     and (pv.effective_to is null or pv.effective_to > v_now)
   order by pv.effective_from desc, pv.captured_at desc limit 1;
  if not found then
    perform infrx.refuse('invalid_request', 'no price snapshot for the requested model');
  end if;
  -- R53: the hold is derived from the snapshot taken in THIS transaction.
  v_hold := infrx.hold_for((r->>'max_input_tokens')::int, (r->>'max_output_tokens')::int,
                           p.input_rate_per_million, p.output_rate_per_million);
  select w.available into v_available from infrx.wallets w
   where w.org_id = (r->>'org_id')::uuid for update;
  if v_hold > coalesce(v_available, 0) then
    perform infrx.refuse('insufficient_credit',
                         'maximum hold exceeds available balance for org ' || (r->>'org_id'));
  end if;
  j := infrx.admission_insert_job(r, v_idem, v_budgets, v_limits, v_deadline, v_now,
    'legacy_usd', r->>'model_revision', p.price_version, jsonb_build_object(
      'price_version', p.price_version, 'currency', p.currency,
      'model_revision', p.model_revision,
      'input_rate_per_million', p.input_rate_per_million::text,
      'output_rate_per_million', p.output_rate_per_million::text,
      'token_rules_version', p.token_rules_version, 'captured_at', p.captured_at),
    '{}'::jsonb, v_hold);
  update infrx.wallets set reserved_total = reserved_total + v_hold, revision = revision + 1,
                           updated_at = v_now
   where org_id = j.org_id;
  insert into infrx.credit_holds (request_id, org_id, key_id, amount, state, created_at,
                                  updated_at)
  values (j.request_id, j.org_id, j.key_id, v_hold, 'held', v_now, v_now);
  perform infrx.admission_rows(r, v_idem, v_limits, j.job_handle, v_now);
  return infrx.job_admission(j.request_id);
end $$;

-- ========================================================== CREDIT admission ===
-- 06a / D1R integration request 2: pins from `resolve_admission_pins` (R69 unpriced ->
-- 22023, R70 private/unknown -> P0002, flag off -> 55000), the wallet resolved from the
-- KEY's identity - never named by the caller (R66) - and a `credit_wallet_holds` row whose
-- trigger reserves (402 = `credit_wallets_reserved_within_total`). Never a zero hold
-- (D1R review (d)).
create or replace function infrx.admit_credit(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r jsonb := p_args->'request';
  v_idem jsonb := p_args->'idem';
  v_limits jsonb := p_args->'limits';
  v_budgets jsonb := p_args->'budgets';
  v_now timestamptz := infrx.now();
  v_replay jsonb;
  v_deadline timestamptz;
  pin record;
  k record;
  w infrx.credit_wallets%rowtype;
  v_hold numeric(20,8);
  j infrx.jobs;
begin
  v_replay := infrx.admission_replay(v_idem, (v_limits->>'idempotency_ttl_s')::float8, v_now);
  if v_replay is not null then
    return v_replay;
  end if;
  if exists (select 1 from infrx.jobs where request_id = (r->>'request_id')::uuid) then
    perform infrx.refuse('state_conflict', 'request ' || (r->>'request_id')
                         || ' is already an admitted job');
  end if;
  v_deadline := infrx.admission_checks(r, v_limits, v_budgets, r->>'model_revision', v_now);
  -- `model_revision` carries what the caller asked for: an alias or an R62 pin.
  select * into pin from infrx.resolve_admission_pins(r->>'model_revision');
  if (r->>'max_input_tokens')::int > pin.max_input_tokens
     or (r->>'max_output_tokens')::int > pin.max_output_tokens then
    perform infrx.refuse('context_length_exceeded',
                         'the request exceeds the deployment''s validated token limits');
  end if;
  -- R66: the wallet is the one the credential's identity owns. A consumer key spends its
  -- individual's wallet through that individual's personal organization; an operator key
  -- spends nothing; a provider_dev key reaches no public listing (its private endpoint
  -- resolution is not built - see the D2 evidence).
  select a.audience, coalesce(a.user_id, a.created_by) as user_id into k
    from public.api_keys a where a.id = (r->>'key_id')::uuid;
  if k.audience = 'operator' then
    perform infrx.refuse('forbidden', 'an operator credential does not spend a wallet');
  end if;
  if k.audience <> 'consumer' then
    perform infrx.refuse('not_found', 'model');
  end if;
  select * into w from infrx.credit_wallets cw
   where cw.owner_user_id = k.user_id and cw.kind = 'consumer' for update;
  if not found then
    perform infrx.refuse('not_found', 'no wallet is provisioned for this credential');
  end if;
  if w.personal_org_id is distinct from (r->>'org_id')::uuid then
    perform infrx.refuse('forbidden', 'the wallet''s personal-org binding does not match '
                         'the organization this key authenticates');
  end if;
  v_hold := infrx.hold_for((r->>'max_input_tokens')::int, (r->>'max_output_tokens')::int,
                           pin.input_rate_per_million::numeric,
                           pin.output_rate_per_million::numeric);
  if v_hold <= 0 then
    perform infrx.refuse('invalid_request', 'a zero hold would meter nothing');
  end if;
  if v_hold > w.available then
    perform infrx.refuse('insufficient_credit',
                         'maximum hold exceeds the wallet''s available CREDIT');
  end if;
  j := infrx.admission_insert_job(r, v_idem, v_budgets, v_limits, v_deadline, v_now,
    'credit', pin.model_revision, null, null, jsonb_build_object(
      'wallet_id', w.wallet_id, 'model_id', pin.model_id,
      'requested_model', pin.requested_model,
      'deployment_revision_id', pin.deployment_revision_id,
      'serving_version_id', pin.serving_version_id,
      'rate_card_version', pin.rate_card_version, 'policy_version', pin.policy_version),
    v_hold);
  insert into infrx.credit_wallet_holds (request_id, org_id, wallet_id, rate_card_version,
                                         amount, state, created_at, updated_at)
  values (j.request_id, j.org_id, j.wallet_id, j.rate_card_version, v_hold, 'held',
          v_now, v_now);
  perform infrx.admission_rows(r, v_idem, v_limits, j.job_handle, v_now);
  return infrx.job_admission(j.request_id);
end $$;

-- ============================================================ the boundary ===
-- 0004's signature and grants (service_role only) are kept; the body routes by the
-- regime the ADAPTER names (the v1 port is legacy_usd; `admit_credit` is CREDIT). A
-- malformed call is invalid_request, never a stub's feature_not_supported.
create or replace function infrx.admit(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if p_args is null or jsonb_typeof(p_args->'request') is distinct from 'object'
     or jsonb_typeof(p_args->'idem') is distinct from 'object'
     or jsonb_typeof(p_args->'limits') is distinct from 'object'
     or jsonb_typeof(p_args->'budgets') is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'admit takes {regime, request, idem, limits, budgets}');
  end if;
  -- R10: the idempotency scope names the request's own organization.
  if p_args->'idem'->>'org_id' is distinct from p_args->'request'->>'org_id' then
    perform infrx.refuse('forbidden', 'the idempotency scope must name the request''s org');
  end if;
  return case p_args->>'regime'
    when 'legacy_usd' then infrx.admit_legacy_usd(p_args)
    when 'credit' then infrx.admit_credit(p_args)
    else infrx.refuse_json('invalid_request', 'unknown accounting regime') end;
end $$;

create or replace function infrx.refuse_json(p_code text, p_detail text) returns jsonb
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  perform infrx.refuse(p_code, p_detail);
  return null;
end $$;

-- ================================================================ privileges ===
-- The boundary keeps 0004's grant (service_role). Everything else here is internal: only
-- a SECURITY DEFINER body calls it, so nobody holds EXECUTE - 0004's default privileges
-- granted service_role, and that is taken back explicitly.
do $$
declare
  f text;
begin
  foreach f in array array[
      'infrx.refuse(text, text, integer)', 'infrx.refuse_json(text, text)',
      'infrx.admission_lock_key()', 'infrx.hold_for(integer, integer, numeric, numeric)',
      'infrx.journal_bytes_charged()', 'infrx.is_entitled(uuid, text)',
      'infrx.admission_replay(jsonb, double precision, timestamptz)',
      'infrx.admission_checks(jsonb, jsonb, jsonb, text, timestamptz)',
      'infrx.admission_rows(jsonb, jsonb, jsonb, text, timestamptz)',
      'infrx.admission_insert_job(jsonb, jsonb, jsonb, jsonb, timestamptz, timestamptz, '
        'text, text, text, jsonb, jsonb, numeric)',
      'infrx.admit_legacy_usd(jsonb)', 'infrx.admit_credit(jsonb)',
      'infrx.jobs_admission_record_guard()']
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role',
                   f);
  end loop;
end $$;
-- The read the adapter's `get_owned` and the harness use.
revoke all on function infrx.job_admission(uuid) from public, anon, authenticated;
grant execute on function infrx.job_admission(uuid) to service_role;
comment on function infrx.admit(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D2 (0011): one '
  'transaction - idempotency, authorization recheck, R29 clamp, ceilings, capacity, price '
  'or pins, derived hold, job + hold + reservations + prepare_dispatch.';
