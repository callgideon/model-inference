-- D5: the terminal transaction, the cancel cause and the operator money operations
-- (D-durable-state §D5; 02 §7 and "Output and unknown outcomes"; 08 §10 R7, R10, R11, R21,
-- R29, R30, R39, R53, R59, R64-R68, R78, R85; oracles DUR-SETTLE, DUR-CAP, DUR-OUTPUT,
-- CREDIT-SPEND, CREDIT-RATE).
--
--   terminalize          the settling transaction behind D3's fence, both regimes: the
--                        argument checks, the identical-proposal replay, the fence, the
--                        validation, then ONE UPDATE of the job (outcome, usage, proposal,
--                        settled_at - 0017's trigger writes the terminal journal event and
--                        re-checks its room, R39), the reservations, the attempt, the
--                        idempotency tombstone, the projections, and last the money in the
--                        job's own unit (`settle_legacy_usd` / `settle_credit`, R64).
--   cancel               0016's body plus the cause (R21): client_cancelled (default),
--                        client_disconnected or sync_deadline; anything else invalid_request.
--   release_aged_unknown 0016's body; it answers `[{"released": outcome}]` (I3B request 5)
--                        so a 24 h release is never read as a new terminalization.
--   claim                0016's body without the MY-3 refusal: a CREDIT job is claimable,
--   load_work_credit     because this fenced read carries its WorkV2 (the admitted pins,
--                        card and data-access policy).
--
-- Settlement is the fake's `_terminalize`, case for case: published with no usage ->
-- held_unknown (hold quarantined for the window); no usage or a cause R21 does not bill ->
-- released (free for the never-charged, platform-absorbed otherwise); `completed` with no
-- usage and nothing published -> engine_incomplete; usage past a token ceiling or a debit
-- past the hold -> platform_error, released; a zero debit -> released_free; else settled at
-- the ADMITTED price or card (R53/R68), rounded half up once to numeric(20,8). Money moves
-- only by inserting a ledger row (the ledger triggers move the totals, D1).
--
-- LOCK ORDER (continues 0011's, 0016's and 0017's): the job row (the fence takes it FOR
-- UPDATE, and the replay's read before the fence takes it the same way), then that job's
-- idempotency row (the tombstone), then its hold, then its wallet. Never the admission
-- scope lock (0011's lock 1): a settlement holds a job row, and admission takes the scope
-- lock first. Admission takes idempotency (2) before a wallet (5) too, so no cycle exists.
--
-- ROLLBACK (0018 alone; nothing before it is edited): restore 0016's bodies of
-- `infrx.terminalize` (the fenced prefix and the `infrx.unimplemented` line), `infrx.cancel`,
-- `infrx.claim` (with MY-3's CREDIT refusal - stop CREDIT admission first, or a claimed
-- CREDIT job has no worker path) and `infrx.release_aged_unknown`, and 0011's
-- `infrx.job_admission` (re-run those
-- `create or replace` statements; grants are kept); drop the functions this file adds
-- (`infrx.load_work_credit`, `infrx.usage_doc`, `infrx.cause_carries_state`,
-- `infrx.debit_legacy_usd`,
-- `infrx.debit_credit`, `infrx.settle_legacy_usd`, `infrx.settle_credit`,
-- `infrx.jobs_settlement_record_guard` with its trigger); `alter table infrx.jobs drop
-- constraint jobs_settled_usage_is_one_fact, drop column proposal, drop column
-- usage_prompt_tokens, drop column usage_completion_tokens`. Settled jobs, ledger rows and
-- usage rows stay: they are money history (append-only), never un-settled.
--
-- Additive and re-runnable.

-- ============================================================ job columns ===
-- The winner's proposal (the fake's `job.proposal`), so its identical retry replays even
-- when the store rewrote the outcome, and the settled usage the outcome carries.
alter table infrx.jobs
  add column if not exists proposal jsonb,
  add column if not exists usage_prompt_tokens int,
  add column if not exists usage_completion_tokens int;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.jobs'::regclass
                 and conname = 'jobs_settled_usage_is_one_fact') then
    alter table infrx.jobs add constraint jobs_settled_usage_is_one_fact
      check ((usage_prompt_tokens is null) = (usage_completion_tokens is null)
             and (usage_prompt_tokens is null
                  or (usage_prompt_tokens >= 0 and usage_completion_tokens >= 0
                      and usage_certainty is not distinct from 'authoritative')));
  end if;
end $$;

-- `jobs_guard` freezes the settled facts of 0003; these three are settled facts too.
create or replace function infrx.jobs_settlement_record_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if old.settled_at is not null
     and (new.proposal is distinct from old.proposal
          or new.usage_prompt_tokens is distinct from old.usage_prompt_tokens
          or new.usage_completion_tokens is distinct from old.usage_completion_tokens) then
    raise exception 'job %: the settled usage and the winning proposal are immutable',
      old.request_id using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger jobs_settlement_record_guard before update on infrx.jobs
  for each row execute function infrx.jobs_settlement_record_guard();

-- ================================================================ helpers ===
-- `records.Usage` as the port dumps it; NULL is "usage unknown".
create or replace function infrx.usage_doc(p_prompt int, p_completion int) returns jsonb
language sql immutable set search_path = infrx, public, pg_temp as $$
  select case when p_prompt is not null then jsonb_build_object(
    'prompt_tokens', p_prompt, 'completion_tokens', p_completion,
    'total_tokens', p_prompt + p_completion, 'certainty', 'authoritative') end;
$$;

-- `records.CAUSE_STATES` (0003's `jobs_cause_matches_state`), answered before anything moves.
create or replace function infrx.cause_carries_state(p_cause text, p_state text)
returns boolean language sql immutable set search_path = infrx, public, pg_temp as $$
  select coalesce(p_state in ('succeeded', 'failed', 'cancelled', 'expired') and case p_cause
    when 'completed' then p_state = 'succeeded'
    when 'client_cancelled' then p_state = 'cancelled'
    when 'client_disconnected' then p_state in ('failed', 'cancelled')
    when 'sync_deadline' then p_state in ('failed', 'cancelled')
    when 'queue_wait_expired' then p_state = 'expired'
    when 'deadline_exceeded' then p_state in ('failed', 'expired')
    when 'invalid_media' then p_state = 'failed'
    when 'preparation_failed' then p_state = 'failed'
    when 'engine_error' then p_state = 'failed'
    when 'engine_incomplete' then p_state = 'failed'
    when 'lost_after_publication' then p_state = 'failed'
    when 'journal_write_failed' then p_state = 'failed'
    when 'retries_exhausted' then p_state = 'failed'
    when 'platform_error' then p_state = 'failed'
    else false end, false);
$$;

-- ================================================= the debit, one per unit ===
-- The ADMITTED rates over the usage, rounded half up ONCE to 1e-8 (`money.debit`):
-- multiplying by 10^-6 is exact in numeric, and round() on a nonnegative numeric is half up.
create or replace function infrx.debit_legacy_usd(p_snapshot jsonb, p_prompt int,
                                                  p_completion int)
returns numeric language sql immutable set search_path = infrx, public, pg_temp as $$
  select round((p_prompt::numeric * (p_snapshot->>'input_rate_per_million')::numeric
                + p_completion::numeric * (p_snapshot->>'output_rate_per_million')::numeric)
               * 0.000001, 8)::numeric(20,8);
$$;

-- R68: the card the job was admitted at (its immutable pin), never the one active now.
create or replace function infrx.debit_credit(p_card text, p_prompt int, p_completion int)
returns numeric language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select round((p_prompt::numeric * c.input_rate_per_million
                + p_completion::numeric * c.output_rate_per_million)
               * 0.000001, 8)::numeric(20,8)
  from infrx.rate_card_versions c where c.rate_card_version = p_card;
$$;

-- ============================================ the money, one body per unit ===
-- Both read the job row the settling UPDATE just wrote, move its hold by its settlement
-- state, insert the debit (settled only) and the one pilot usage row, every timestamp the
-- job's `settled_at` (= infrx.now(); the `created_at` defaults are now(), D1).
create or replace function infrx.settle_legacy_usd(p_id uuid, p_debit numeric)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
  h infrx.credit_holds%rowtype;
begin
  select * into j from infrx.jobs where request_id = p_id;
  if j.settlement_state = 'held_unknown' then
    perform infrx.quarantine_hold_legacy_usd(p_id, j.reconcile_after);
  elsif j.settlement_state = 'settled' then
    update infrx.credit_holds set state = 'settled', updated_at = j.settled_at
     where request_id = p_id and state = 'held'
    returning * into h;
    if not found then
      raise exception 'job %: its USD hold is not held', p_id using errcode = '23514';
    end if;
    update infrx.wallets set reserved_total = reserved_total - h.amount,
                             revision = revision + 1, updated_at = j.settled_at
     where org_id = h.org_id;
    -- The one writer of ledger_total is the ledger row's trigger (0003).
    insert into public.credit_ledger (org_id, delta_usd, kind, reason, ref, request_id,
                                      created_at)
    values (j.org_id, -p_debit, 'usage', 'inference usage', j.job_handle, p_id,
            j.settled_at);
  else
    perform infrx.release_hold_legacy_usd(j.request_id);
  end if;
  insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream,
    prompt_tokens, completion_tokens, cost_usd, created_at, settlement_regime, outcome,
    job_state, settlement_state, usage_certainty, price_version, settlement_version,
    execution_mode, trace_mode)
  values (p_id, j.org_id, j.key_id, split_part(j.model_revision, '@', 1),
    case j.state when 'succeeded' then 200 when 'cancelled' then 499 else 500 end,
    j.execution_mode = 'stream', j.usage_prompt_tokens, j.usage_completion_tokens, p_debit,
    j.settled_at, 'pilot', j.outcome_cause, j.state, j.settlement_state, j.usage_certainty,
    j.price_version, 1, j.execution_mode, j.trace_mode);
end $$;

-- D1R request 4 / D2's hard rule (`credit_schema.D5_SETTLE_HOLD_BEFORE_DEBIT`): the hold
-- held -> settled FIRST (its trigger releases the reservation), THEN the inference debit.
-- A retired individual's frozen wallet still settles a pre-retirement hold (R85): 0015
-- guards only a new hold and a signup grant.
create or replace function infrx.settle_credit(p_id uuid, p_charged numeric)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
begin
  select * into j from infrx.jobs where request_id = p_id;
  if j.settlement_state = 'held_unknown' then
    perform infrx.quarantine_hold_credit(p_id, j.reconcile_after);
  elsif j.settlement_state = 'settled' then
    update infrx.credit_wallet_holds set state = 'settled'
     where request_id = p_id and state = 'held';
    if not found then
      raise exception 'job %: its CREDIT hold is not held', p_id using errcode = '23514';
    end if;
    insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,
                                     request_id, actor, reason, created_at)
    select j.wallet_id, w.kind, 'inference_debit', -p_charged, p_id, p_id, 'platform',
           'inference', j.settled_at
      from infrx.credit_wallets w where w.wallet_id = j.wallet_id;
  else
    perform infrx.release_hold_credit(j.request_id);
  end if;
  insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream,
    prompt_tokens, completion_tokens, created_at, settlement_regime, outcome, job_state,
    settlement_state, usage_certainty, settlement_version, execution_mode, trace_mode,
    accounting_regime, charged_credits, rate_card_version, serving_version_id,
    deployment_revision_id)
  select p_id, j.org_id, j.key_id, m.id,
    case j.state when 'succeeded' then 200 when 'cancelled' then 499 else 500 end,
    j.execution_mode = 'stream', j.usage_prompt_tokens, j.usage_completion_tokens,
    j.settled_at, 'pilot', j.outcome_cause, j.state, j.settlement_state, j.usage_certainty, 1,
    j.execution_mode, j.trace_mode, 'credit', p_charged, j.rate_card_version,
    j.serving_version_id, j.deployment_revision_id
  from public.models m where m.model_uuid = j.model_id;
  if not found then
    raise exception 'job %: its model % is not in the catalog', p_id, j.model_id
      using errcode = '23503';
  end if;
end $$;

-- ================================================== the admission document ===
-- 0011's `job_admission`, plus the settled `usage` in the outcome and, for a CREDIT job,
-- the charge its inference debit recorded (`SettlementV2.charged`).
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
    'prepared_prompt_tokens', j.prepared_prompt_tokens,
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
        'usage', infrx.usage_doc(j.usage_prompt_tokens, j.usage_completion_tokens),
        'result_ref', j.result_ref, 'settlement_state', j.settlement_state,
        'debit', j.debit::text, 'settled_at', j.settled_at,
        'reconcile_after', j.reconcile_after) end,
    'charged_credits', (select (-l.amount)::text from infrx.credit_ledger l
                         where l.request_id = j.request_id and l.kind = 'inference_debit'),
    'replayed', false)
  from infrx.jobs j where j.request_id = p_request_id;
$$;

-- ============================================================ terminalize ===
-- Args `{lease, outcome, regime, limits}` (`outcome` is the worker's proposal, a
-- `records.TerminalOutcome`); answers the job's admission document (its `outcome` is the
-- committed one) or `{"refusal": …}` after an R29 terminalization (R39).
create or replace function infrx.terminalize(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  o jsonb := p_args->'outcome';
  u jsonb := nullif(p_args->'outcome'->'usage', 'null'::jsonb);
  v_regime text := coalesce(p_args->>'regime', 'legacy_usd');
  v_now timestamptz := infrx.now();
  v_reconcile_s float8;
  v_result_ttl_s float8;
  v_idem_ttl_s float8;
  v_refusal jsonb;
  v_proposal jsonb;
  v_cause text := p_args->'outcome'->>'cause';
  v_state text := p_args->'outcome'->>'state';
  v_ref text := p_args->'outcome'->>'result_ref';
  v_in int;
  v_out int;
  v_settlement text;
  v_charge numeric(20,8) := 0;
  v_reconcile timestamptz;
  j infrx.jobs%rowtype;
begin
  -- 0. The fake's argument checks, before anything is read (R30, R10).
  if jsonb_typeof(p_args->'lease') is distinct from 'object'
     or jsonb_typeof(o) is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'terminalize takes {lease, outcome, regime, limits}');
  end if;
  if v_state = 'succeeded' and v_ref is null then
    perform infrx.refuse('invalid_request', 'a succeeded outcome requires a result reference');
  end if;
  if o->>'job_id' is distinct from p_args->'lease'->>'job_id' then
    perform infrx.refuse('invalid_request', 'the outcome does not belong to the leased job');
  end if;
  v_reconcile_s := infrx.lease_limit(p_args, 'unknown_usage_reconcile_s');
  v_result_ttl_s := infrx.lease_limit(p_args, 'result_ttl_s');
  v_idem_ttl_s := infrx.lease_limit(p_args, 'idempotency_ttl_s');
  -- The proposal as `(cause, usage, result_ref)`, the usage without its record version, so
  -- it compares with the committed triple (`usage_doc`).
  v_proposal := jsonb_build_object('cause', v_cause, 'usage',
                                   case when jsonb_typeof(u) = 'object'
                                        then u - 'schema_version' else u end,
                                   'result_ref', v_ref);
  -- 1. The regime, then the identical-proposal replay: the winner's retry answers the
  -- committed outcome even when the store rewrote it (a fence would refuse a terminal job).
  select * into j from infrx.jobs where request_id = (o->>'job_id')::uuid for update;
  if found then
    if j.accounting_regime <> v_regime then
      perform infrx.refuse('not_found', 'job ' || j.request_id || ' is not settled in '
                           || v_regime);
    end if;
    if j.settled_at is not null and (v_proposal = j.proposal or v_proposal =
        jsonb_build_object('cause', j.outcome_cause, 'usage',
                           infrx.usage_doc(j.usage_prompt_tokens, j.usage_completion_tokens),
                           'result_ref', j.result_ref)) then
      return infrx.job_admission(j.request_id);
    end if;
  end if;
  -- 2. D3's fence: stale, foreign, expired, wrong-kind refused; R29 terminalizes, commits
  -- and answers the refusal as data (R39). The job row is locked from here on.
  v_refusal := infrx.fence_lease(p_args->'lease', array['inference'], v_reconcile_s);
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  select * into j from infrx.jobs where request_id = (p_args->'lease'->>'job_id')::uuid;
  -- 3. Validation before any mutation.
  if not infrx.cause_carries_state(v_cause, v_state) then
    perform infrx.refuse('state_conflict', 'cause ' || coalesce(v_cause, 'null')
                         || ' cannot carry state ' || coalesce(v_state, 'null'));
  end if;
  if u is not null then
    if jsonb_typeof(u) is distinct from 'object'
       or u->>'certainty' is distinct from 'authoritative'
       or jsonb_typeof(u->'prompt_tokens') is distinct from 'number'
       or jsonb_typeof(u->'completion_tokens') is distinct from 'number'
       or (u->>'prompt_tokens')::numeric not between 0 and 2147483647
       or (u->>'completion_tokens')::numeric not between 0 and 2147483647
       or (u->>'prompt_tokens')::numeric % 1 <> 0
       or (u->>'completion_tokens')::numeric % 1 <> 0 then
      perform infrx.refuse('invalid_request', 'a present usage is authoritative token counts');
    end if;
    v_in := (u->>'prompt_tokens')::int;
    v_out := (u->>'completion_tokens')::int;
  end if;
  -- R30: a result reference names THIS job's stored, immutable result (0014).
  if v_ref is not null and (v_ref <> 'infrx-result:' || j.request_id
      or not exists (select 1 from infrx.job_results r
                      where r.request_id = j.request_id and r.org_id = j.org_id)) then
    perform infrx.refuse('invalid_request', 'the result reference is not this job''s '
                         || 'stored result');
  end if;
  -- 4. The settlement (the fake's `_terminalize`).
  if v_in is null and v_cause = 'completed' and not j.published then
    -- A delivered success nobody metered and nothing published is an incomplete run.
    v_cause := 'engine_incomplete';
    v_state := 'failed';
    v_ref := null;
  end if;
  if v_in is null and j.published then
    -- Output nobody counted: reconciled after the window, never billed later (02).
    v_settlement := 'held_unknown';
    v_reconcile := v_now + make_interval(secs => v_reconcile_s);
  elsif v_in is null
        or v_cause not in ('completed', 'client_cancelled', 'client_disconnected') then
    -- R21: only three causes bill, and only with authoritative usage.
    v_settlement := case when v_cause in ('invalid_media', 'preparation_failed',
                                          'queue_wait_expired')
                            or (v_in is null and v_cause in ('completed', 'client_cancelled',
                                                             'client_disconnected'))
                         then 'released_free' else 'released_platform_absorbed' end;
  else
    v_charge := case j.accounting_regime
                  when 'credit' then infrx.debit_credit(j.rate_card_version, v_in, v_out)
                  else infrx.debit_legacy_usd(j.price_snapshot, v_in, v_out) end;
    if v_in > j.max_input_tokens or v_out > j.max_output_tokens
       or v_charge > j.maximum_hold then
      -- Beyond the reserved envelope: a platform failure, never an unreserved debit.
      v_cause := 'platform_error';
      v_state := 'failed';
      v_settlement := 'released_platform_absorbed';
      v_in := null;
      v_out := null;
      v_ref := null;
      v_charge := 0;
    elsif v_charge = 0 then
      v_settlement := 'released_free';
    else
      v_settlement := 'settled';
    end if;
  end if;
  if v_settlement <> 'settled' then
    v_charge := 0;
  end if;
  -- ONE update: settled_at is set once, with the outcome, so 0017's trigger writes the one
  -- terminal event from this row (and refuses the whole transaction without room, R39).
  -- A CREDIT job's v1 debit is 0: its charge is CREDIT, in its own ledger (R64).
  update infrx.jobs
     set state = v_state, outcome_cause = v_cause, settlement_state = v_settlement,
         settled_at = v_now, result_ref = v_ref,
         debit = case when j.accounting_regime = 'credit' then 0 else v_charge end,
         result_expires_at = case when v_state = 'succeeded'
                                  then v_now + make_interval(secs => v_result_ttl_s) end,
         usage_certainty = case when v_in is not null then 'authoritative'
                                when v_settlement = 'held_unknown' then 'unknown' end,
         usage_prompt_tokens = v_in, usage_completion_tokens = v_out,
         reconcile_after = v_reconcile, queued_at = null, proposal = v_proposal
   where request_id = j.request_id;
  update infrx.capacity_reservations set active = false, released_at = v_now
   where request_id = j.request_id and active;
  update infrx.attempts set released_at = v_now, finished_at = coalesce(finished_at, v_now)
   where job_id = j.request_id and released_at is null;
  -- The tombstone: the mapping is kept idempotency_ttl_s after the terminal state (01).
  update infrx.idempotency set expires_at = v_now + make_interval(secs => v_idem_ttl_s)
   where request_id = j.request_id;
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), j.request_id, j.org_id, 'usage_projection',
          jsonb_build_object('request_id', j.request_id, 'settlement_state', v_settlement),
          v_now),
         (gen_random_uuid(), j.request_id, j.org_id, 'trace_projection',
          jsonb_build_object('request_id', j.request_id), v_now);
  -- The money, last, in the job's own unit: hold, then wallet (no body reads both, R64).
  if j.accounting_regime = 'credit' then
    perform infrx.settle_credit(j.request_id, v_charge);
  else
    perform infrx.settle_legacy_usd(j.request_id, v_charge);
  end if;
  return infrx.job_admission(j.request_id);
end $$;

-- ================================================================= cancel ===
-- 0016's body; the one change is the cause (R21), checked before anything is read. A
-- repeat cancel with another cause answers the committed outcome (the first cause wins).
create or replace function infrx.cancel(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
  v_cause text := coalesce(p_args->>'cause', 'client_cancelled');
begin
  if p_args->>'org_id' is null or p_args->>'job_handle' is null then
    perform infrx.refuse('invalid_request', 'cancel takes {org_id, job_handle, limits}');
  end if;
  if v_cause not in ('client_cancelled', 'client_disconnected', 'sync_deadline') then
    perform infrx.refuse('invalid_request', v_cause || ' is not a cancellation cause');
  end if;
  select * into j from infrx.jobs
   where job_handle = p_args->>'job_handle' and org_id = (p_args->>'org_id')::uuid
   for update;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || (p_args->>'job_handle') || ' owned by org '
                         || (p_args->>'org_id'));
  end if;
  if j.settled_at is not null then
    return infrx.job_admission(j.request_id)->'outcome';     -- the committed outcome wins
  end if;
  return infrx.terminalize_no_usage(j.request_id, v_cause, 'cancelled',
                                    infrx.lease_limit(p_args, 'unknown_usage_reconcile_s'));
end $$;

-- ====================================================== the 24 h release ===
-- 0016's body; the one change is the answer: `released`, never `outcome` (I3B request 5).
create or replace function infrx.release_aged_unknown(p_id uuid, p_now timestamptz)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
begin
  -- Rechecked under the lock: a concurrent sweep may have released it already.
  select * into j from infrx.jobs
   where request_id = p_id and settlement_state = 'held_unknown'
   for update skip locked;
  if not found then
    return '[]';
  end if;
  if j.accounting_regime = 'credit' then
    perform infrx.release_hold_credit(p_id);
  else
    perform infrx.release_hold_legacy_usd(p_id);
  end if;
  update infrx.jobs set settlement_state = 'released_platform_absorbed',
                        reconcile_after = null
   where request_id = p_id;
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), p_id, j.org_id, 'usage_projection',
          jsonb_build_object('request_id', p_id,
                             'settlement_state', 'released_platform_absorbed'), p_now);
  return jsonb_build_array(jsonb_build_object('released',
                                              infrx.job_admission(p_id)->'outcome'));
end $$;

-- ================================================================== claim ===
-- 0016's body; the one change is WorkV2 (D5 item 2): the MY-3 refusal of a CREDIT job is
-- gone, because `load_work_credit` now carries its work. Args `{job_id, worker_id, limits}`;
-- answers `{"lease": …}`.
create or replace function infrx.claim(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_worker text := p_args->>'worker_id';
  v_ttl float8;
  v_generation_deadline timestamptz;
  j infrx.jobs%rowtype;
  a infrx.attempts%rowtype;
begin
  if p_args->>'job_id' is null or v_worker is null or length(btrim(v_worker)) = 0 then
    perform infrx.refuse('invalid_request', 'claim takes {job_id, worker_id, limits}');
  end if;
  v_ttl := infrx.lease_limit(p_args, 'lease_ttl_s');
  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || (p_args->>'job_id'));
  end if;
  if j.settled_at is not null then
    perform infrx.refuse('already_terminal', 'job ' || j.request_id || ' is ' || j.state);
  end if;
  if j.state <> 'queued' then
    perform infrx.refuse('not_claimable', 'job ' || j.request_id || ' is ' || j.state
                         || ', not queued');
  end if;
  -- Either-kind exclusion from the attempts table as well as the state (review FE-4): a job
  -- anyone still holds a lease on is not claimable, whatever its state column says.
  if exists (select 1 from infrx.attempts where job_id = j.request_id
                and released_at is null) then
    perform infrx.refuse('not_claimable', 'job ' || j.request_id || ' has a live attempt');
  end if;
  if v_now >= j.deadline_at then
    perform infrx.refuse('not_claimable', 'job ' || j.request_id
                         || ' is past its absolute deadline');
  end if;
  -- R20: the persisted instant. `recover` terminalizes the job meanwhile.
  if v_now >= j.queue_deadline_at then
    perform infrx.refuse('not_claimable', 'job ' || j.request_id
                         || ' is past its queue deadline');
  end if;
  -- R38: the queued interval that ends here is charged to the queue budget.
  update infrx.jobs set state = 'running', queued_at = null,
                        queue_wait_used_s = queue_wait_used_s
                          + coalesce(extract(epoch from v_now - j.queued_at)::float8, 0)
   where request_id = j.request_id;
  -- R20: the generation phase starts at the claim; both instants never pass deadline_at.
  v_generation_deadline := least(v_now + make_interval(secs => j.budget_generation_s),
                                 j.deadline_at);
  insert into infrx.attempts (job_id, kind, generation, worker_id, retry_ordinal,
                              acquired_at, expires_at, generation_deadline_at,
                              first_token_deadline_at)
  values (j.request_id, 'inference',
          (select coalesce(max(generation), 0) + 1 from infrx.attempts
            where job_id = j.request_id and kind = 'inference'),
          v_worker, j.attempts, v_now, v_now + make_interval(secs => v_ttl),
          v_generation_deadline,
          least(v_now + make_interval(secs => j.budget_first_token_s), v_generation_deadline))
  returning * into a;
  return jsonb_build_object('lease', infrx.lease_doc(a));
end $$;

-- ============================================================ WorkV2 (D) ===
-- Args `{lease, limits}`; answers `{request, prepared_refs, admission, policy}` or a refusal:
-- `load_work`'s fence and document plus the job's PINNED data-access policy. Everything is
-- the admitted job's (pins, card, policy - R68/R78), never what the catalog serves now.
create or replace function infrx.load_work_credit(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_refusal jsonb;
  j infrx.jobs%rowtype;
begin
  if jsonb_typeof(p_args->'lease') is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'load_work_credit takes {lease, limits}');
  end if;
  v_refusal := infrx.fence_lease(p_args->'lease', array['preparation', 'inference'],
                                 infrx.lease_limit(p_args, 'unknown_usage_reconcile_s'));
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  select * into j from infrx.jobs where request_id = (p_args->'lease'->>'job_id')::uuid;
  return jsonb_build_object(
    'request', j.request_record || jsonb_build_object('deadline_at', j.deadline_at),
    'prepared_refs', coalesce(j.prepared_refs, '[]'),
    'admission', infrx.job_admission(j.request_id),
    'policy', (select jsonb_build_object('policy_version', p.policy_version,
                                         'consent_version', j.consent_version,
                                         'trace_mode', j.trace_mode,
                                         'effective_at', p.effective_at)
                 from infrx.data_access_policies p where p.policy_version = j.policy_version));
end $$;

-- ================================================================ privileges ===
-- `terminalize`, `cancel`, `claim` keep 0004's grant and `job_admission` 0011's
-- (service_role only), `release_aged_unknown` 0016's (nobody): `create or replace` preserves
-- them. `load_work_credit` is a platform operation: service_role only.
-- Everything new here is internal: only a SECURITY DEFINER body or a trigger calls it.
do $$
declare
  f text;
begin
  foreach f in array array[
      'infrx.jobs_settlement_record_guard()', 'infrx.usage_doc(integer, integer)',
      'infrx.cause_carries_state(text, text)',
      'infrx.debit_legacy_usd(jsonb, integer, integer)',
      'infrx.debit_credit(text, integer, integer)',
      'infrx.settle_legacy_usd(uuid, numeric)', 'infrx.settle_credit(uuid, numeric)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role',
                   f);
  end loop;
  foreach f in array array['infrx.load_work_credit(jsonb)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
end $$;
comment on function infrx.terminalize(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; fenced prefix D3 (0016); the '
  'settling transaction D5 (0018): replay, fence, validation, one settling update, money.';
comment on function infrx.cancel(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D3 (0016), cause D5 '
  '(0018): cancellation with its R21 cause, released or quarantined in one transaction.';
