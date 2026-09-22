-- D3: fenced leases, recovery and cancellation (D-durable-state §D3; 02 §3-§6;
-- 08 §10 R7, R20, R21, R29, R38, R39, R46, R52, R55; oracles DUR-FENCE, DUR-OUTPUT).
--
--   fence_lease          THE fence every execution mutation runs first (internal): the
--                        job row FOR UPDATE, then kind, terminal, live attempt,
--                        generation, owner, R29's phase deadline (terminalize, commit,
--                        refuse - R39/R55) and only then lease expiry. D4's `append` and
--                        D5's settlement call it as their first statement.
--   claim                `queued -> running`: the next inference generation, minted under
--                        the job row lock, with its R20 instants and R38 queue accounting.
--   heartbeat            renews the STORED lease on the database clock (R29: the caller's
--                        copy is only a fencing token); a preparation lease never past
--                        `preparation_deadline_at` (R52).
--   load_work            the fenced read of what a lease holder executes (R46).
--   cancel               any nonterminal state -> `cancelled`, releasing the hold and every
--                        reservation in the same transaction; a job already terminal answers
--                        its committed outcome (completion won the race).
--   terminalize          the settlement boundary (D5's body): D3 fills its FENCED PREFIX,
--                        so a stale, foreign, expired, wrong-kind or overdue lease is refused
--                        (or terminalized, R29) now; the settlement after the fence is still
--                        `infrx.unimplemented(..., 'D5')`.
--   recover              the reaper (R7: no caller time). Lost preparation workers are
--                        reaped and redispatched within MAX_PREPUBLICATION_RETRIES; lost
--                        inference attempts are requeued only BEFORE publication, within the
--                        retry counter and the phase instants; published ones fail
--                        `lost_after_publication`; aged unknown-usage holds are released
--                        platform-absorbed. One job that cannot be reaped never stops the
--                        sweep (it is reported, not returned).
--
-- Every terminalization here is `terminalize_no_usage`: nothing is debited (R21 - no
-- authoritative usage, no charge). Unpublished: released (free for the free causes and a
-- client's own cancellation, platform-absorbed otherwise). Published: `held_unknown` with
-- the 24 h window (02: output nobody counted is reconciled, never billed later).
--
-- LOCK ORDER (continues 0011's): a job row, then its hold, then the wallet (the D2 rule for
-- settlement: "5 without 1"). A request touches one job row, except `recover`, which takes
-- job rows with SKIP LOCKED and therefore never waits on one while holding a wallet - a
-- job whose heartbeat, cancel or claim is in flight is simply left to the next sweep.
--
-- The terminal JOURNAL event is not written here: the journal is D4's, and D5's settling
-- transaction writes it. Until then a terminal job has an outcome and projections but no
-- terminal chunk (see the D3 evidence, limits).
--
-- Amendment in place (0003, unapplied to any hosted project - hosted carries 0001-0002;
-- same basis as D2's 0003 amendment): `jobs_guard` permits exactly one change of a
-- terminal settlement, held_unknown -> released_platform_absorbed, which is the reaper's
-- release of the 24 h window. Everything else about a terminal job stays immutable.
--
-- Additive and re-runnable.

-- ================================================================== helpers ===
-- A duration limit the adapter sends (the store's own configuration, never a caller's).
create or replace function infrx.lease_limit(p_args jsonb, p_name text) returns float8
language plpgsql immutable set search_path = infrx, public, pg_temp as $$
declare
  v float8 := (p_args->'limits'->>p_name)::float8;
begin
  if v is null or v < 0 then
    perform infrx.refuse('invalid_request', 'limits.' || p_name || ' is required');
  end if;
  return v;
end $$;

-- The unknown-usage window on a hold, one function per unit (R64/R65: no body reads both
-- money tables). The reservation stays: an unknown hold is still reserved.
create or replace function infrx.quarantine_hold_legacy_usd(p_request_id uuid,
                                                            p_reconcile_after timestamptz)
returns void language sql security definer set search_path = infrx, public, pg_temp as $$
  update infrx.credit_holds set state = 'unknown', reconcile_after = p_reconcile_after,
                                updated_at = infrx.now()
   where request_id = p_request_id and state = 'held';
$$;

create or replace function infrx.quarantine_hold_credit(p_request_id uuid,
                                                        p_reconcile_after timestamptz)
returns void language sql security definer set search_path = infrx, public, pg_temp as $$
  update infrx.credit_wallet_holds set state = 'unknown', reconcile_after = p_reconcile_after
   where request_id = p_request_id and state = 'held';
$$;

-- ================================================== terminalize without usage ===
-- A nonterminal job -> terminal, with no usage and no debit, in the caller's transaction.
-- Every caller has checked, under the job row lock, that the job is not terminal; a caller
-- that forgot would be refused by `jobs_guard` (a terminal job is immutable). Returns the
-- committed outcome (`records.TerminalOutcome`).
create or replace function infrx.terminalize_no_usage(p_request_id uuid, p_cause text,
                                                      p_state text, p_reconcile_s float8)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
  v_now timestamptz := infrx.now();
  v_settlement text;
  v_reconcile timestamptz;
begin
  select * into j from infrx.jobs where request_id = p_request_id for update;
  if j.published then
    -- Output was committed and nobody counted it: reconcile, never bill later (02, R21).
    v_settlement := 'held_unknown';
    v_reconcile := v_now + make_interval(secs => p_reconcile_s);
  elsif p_cause in ('invalid_media', 'preparation_failed', 'queue_wait_expired',
                    'client_cancelled', 'client_disconnected') then
    -- The customer was never going to be charged: nothing ran, or they left first.
    v_settlement := 'released_free';
  else
    v_settlement := 'released_platform_absorbed';
  end if;
  update infrx.jobs set state = p_state, outcome_cause = p_cause,
                        settlement_state = v_settlement, settled_at = v_now,
                        usage_certainty = case when j.published then 'unknown' end,
                        reconcile_after = v_reconcile, queued_at = null
   where request_id = p_request_id;
  if v_settlement = 'held_unknown' and j.accounting_regime = 'credit' then
    perform infrx.quarantine_hold_credit(p_request_id, v_reconcile);
  elsif v_settlement = 'held_unknown' then
    perform infrx.quarantine_hold_legacy_usd(p_request_id, v_reconcile);
  elsif j.accounting_regime = 'credit' then
    perform infrx.release_hold_credit(p_request_id);
  else
    perform infrx.release_hold_legacy_usd(p_request_id);
  end if;
  update infrx.capacity_reservations set active = false, released_at = v_now
   where request_id = p_request_id and active;
  update infrx.attempts set released_at = v_now, finished_at = coalesce(finished_at, v_now)
   where job_id = p_request_id and released_at is null;
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), p_request_id, j.org_id, 'usage_projection',
          jsonb_build_object('request_id', p_request_id, 'settlement_state', v_settlement),
          v_now),
         (gen_random_uuid(), p_request_id, j.org_id, 'trace_projection',
          jsonb_build_object('request_id', p_request_id), v_now);
  return infrx.job_admission(p_request_id)->'outcome';
end $$;

-- ============================================================= the fence ===
-- `p_lease` is the caller's `records.Lease`: a fencing token, never a record (R29).
-- Plain refusals raise. A refusal that follows a committed terminalization is RETURNED
-- (`{code, detail}`), because raising would roll the terminalization back (R39); NULL
-- means the fence holds and the job row is locked by this transaction.
create or replace function infrx.fence_lease(p_lease jsonb, p_kinds text[],
                                             p_reconcile_s float8)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_kind text := p_lease->>'kind';
  j infrx.jobs%rowtype;
  a infrx.attempts%rowtype;
begin
  if jsonb_typeof(p_lease) is distinct from 'object' or p_lease->>'job_id' is null then
    perform infrx.refuse('invalid_request', 'a lease is required');
  end if;
  -- R46: the two attempt sequences have separate counters, so a lease of the wrong kind
  -- would otherwise pass as the same generation of the other one.
  if v_kind is null or not (v_kind = any(p_kinds)) then
    perform infrx.refuse('stale_lease', coalesce(v_kind, 'no') ||
                         ' lease cannot fence this operation');
  end if;
  select * into j from infrx.jobs where request_id = (p_lease->>'job_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || (p_lease->>'job_id'));
  end if;
  if j.settled_at is not null then
    perform infrx.refuse('already_terminal', 'job ' || j.request_id || ' is already '
                         || j.state);
  end if;
  select * into a from infrx.attempts
   where job_id = j.request_id and kind = v_kind and released_at is null;
  -- A live attempt of this kind exists exactly while the job is in the phase it fences.
  if not found then
    perform infrx.refuse('stale_lease', 'job ' || j.request_id || ' is ' || j.state
                         || ' with no live ' || v_kind || ' lease');
  end if;
  if a.generation is distinct from (p_lease->>'generation')::int then
    perform infrx.refuse('stale_lease', v_kind || ' generation ' || (p_lease->>'generation')
                         || ' != ' || a.generation);
  end if;
  -- A different worker at the same generation is still the wrong worker.
  if a.worker_id is distinct from p_lease->>'worker_id' then
    perform infrx.refuse('stale_lease', 'the lease belongs to ' || a.worker_id);
  end if;
  -- R29/R55: the phase deadline BEFORE expiry, from the STORED instants, and the job is
  -- terminalized in this same operation.
  if v_kind = 'preparation' and v_now >= j.preparation_deadline_at then
    perform infrx.terminalize_no_usage(j.request_id, 'preparation_failed', 'failed',
                                       p_reconcile_s);
    return jsonb_build_object('code', 'already_terminal', 'detail',
                              'job ' || j.request_id || ' passed its preparation deadline');
  end if;
  if v_now >= j.deadline_at or v_now >= a.generation_deadline_at then
    perform infrx.terminalize_no_usage(j.request_id, 'deadline_exceeded', 'failed',
                                       p_reconcile_s);
    return jsonb_build_object('code', 'already_terminal', 'detail',
                              'job ' || j.request_id || ' passed its deadline');
  end if;
  if v_now >= a.expires_at then
    perform infrx.refuse('stale_lease', v_kind || ' lease expired at ' || a.expires_at);
  end if;
  return null;
end $$;

-- ================================================================== claim ===
-- Args `{job_id, worker_id, limits}`; answers `{"lease": …}`.
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

-- ============================================================== heartbeat ===
-- Args `{lease, limits}`; answers `{"lease": …}` (the STORED lease, renewed) or
-- `{"refusal": …}` after an R29 terminalization.
create or replace function infrx.heartbeat(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_refusal jsonb;
  a infrx.attempts%rowtype;
begin
  if jsonb_typeof(p_args->'lease') is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'heartbeat takes {lease, limits}');
  end if;
  v_refusal := infrx.fence_lease(p_args->'lease', array['preparation', 'inference'],
                                 infrx.lease_limit(p_args, 'unknown_usage_reconcile_s'));
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  update infrx.attempts t
     set expires_at = case t.kind
           when 'preparation' then least(v_now + make_interval(
                  secs => infrx.lease_limit(p_args, 'preparation_lease_ttl_s')),
                  t.generation_deadline_at)          -- = preparation_deadline_at (R52)
           else v_now + make_interval(secs => infrx.lease_limit(p_args, 'lease_ttl_s')) end
   where t.job_id = (p_args->'lease'->>'job_id')::uuid and t.kind = p_args->'lease'->>'kind'
     and t.released_at is null
  returning * into a;
  return jsonb_build_object('lease', infrx.lease_doc(a));
end $$;

-- ============================================================== load_work ===
-- Args `{lease, limits}`; answers `{request, prepared_refs, admission}` or a refusal.
-- The request carries the deadline the store keeps (R29's clamp), like the fake's.
create or replace function infrx.load_work(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_refusal jsonb;
  j infrx.jobs%rowtype;
begin
  if jsonb_typeof(p_args->'lease') is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'load_work takes {lease, limits}');
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
    'admission', infrx.job_admission(j.request_id));
end $$;

-- ================================================================= cancel ===
-- Args `{org_id, job_handle, limits}`; answers the committed outcome. Ownership is the
-- WHERE clause: another tenant's handle and an unknown one are the same `not_found`.
create or replace function infrx.cancel(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
begin
  if p_args->>'org_id' is null or p_args->>'job_handle' is null then
    perform infrx.refuse('invalid_request', 'cancel takes {org_id, job_handle, limits}');
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
  return infrx.terminalize_no_usage(j.request_id, 'client_cancelled', 'cancelled',
                                    infrx.lease_limit(p_args, 'unknown_usage_reconcile_s'));
end $$;

-- ====================================== terminalize: the fenced prefix (D5 fills) ===
-- Args `{lease, outcome, limits}`. D5 replaces the line after the fence with the
-- settlement (and puts its identical-proposal replay in front of the fence).
create or replace function infrx.terminalize(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_refusal jsonb;
begin
  if jsonb_typeof(p_args->'lease') is distinct from 'object'
     or jsonb_typeof(p_args->'outcome') is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'terminalize takes {lease, outcome, limits}');
  end if;
  v_refusal := infrx.fence_lease(p_args->'lease', array['inference'],
                                 infrx.lease_limit(p_args, 'unknown_usage_reconcile_s'));
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  perform infrx.unimplemented('terminalize (the settlement after the D3 fence)', 'D5');
  return null;
end $$;

-- ================================================================ recover ===
-- One job, locked SKIP LOCKED (a job whose own transaction is in flight is the next
-- sweep's), decided from its fresh row. Answers a jsonb array of 0 or 1 items.
create or replace function infrx.recover_job(p_id uuid, p_now timestamptz, p_retries int,
                                             p_reconcile_s float8)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
  a infrx.attempts%rowtype;
  o infrx.outbox%rowtype;
begin
  select * into j from infrx.jobs
   where request_id = p_id and settled_at is null for update skip locked;
  if not found then
    return '[]';
  end if;
  if j.state = 'preparing' then
    -- R29/R20: a preparation that never comes back is the reaper's.
    if p_now >= j.preparation_deadline_at then
      return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(
        p_id, 'preparation_failed', 'failed', p_reconcile_s)));
    end if;
    select * into a from infrx.attempts
     where job_id = p_id and kind = 'preparation' and released_at is null;
    if not found or p_now < a.expires_at then
      return '[]';
    end if;
    update infrx.attempts set released_at = p_now, finished_at = coalesce(finished_at, p_now)
     where job_id = p_id and kind = 'preparation' and generation = a.generation;
    -- R46/R52: the first claim plus MAX_PREPUBLICATION_RETRIES further ones; after the
    -- last permitted loss a redispatch could only terminalize, so settle it here.
    if j.preparation_attempts > p_retries then
      return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(
        p_id, 'preparation_failed', 'failed', p_reconcile_s)));
    end if;
    insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
    values (gen_random_uuid(), p_id, j.org_id, 'prepare_dispatch',
            jsonb_build_object('job_handle', j.job_handle, 'request_id', p_id,
                               'attempt', j.preparation_attempts), p_now);
    return '[]';
  end if;
  if j.state = 'queued' then
    if p_now >= j.queue_deadline_at then
      return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(
        p_id, 'queue_wait_expired', 'expired', p_reconcile_s)));
    end if;
    return '[]';
  end if;
  select * into a from infrx.attempts
   where job_id = p_id and kind = 'inference' and released_at is null;
  if not found then
    return '[]';
  end if;
  -- R20/R21: past the generation instant (itself capped by deadline_at) the attempt is
  -- over whether or not its lease is live; our own deadline, so platform-absorbed.
  if p_now >= a.generation_deadline_at then
    return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(
      p_id, 'deadline_exceeded', 'failed', p_reconcile_s)));
  end if;
  if p_now < a.expires_at then
    return '[]';
  end if;
  -- 02 §6: after the publication marker, never regenerate.
  if j.published then
    return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(
      p_id, 'lost_after_publication', 'failed', p_reconcile_s)));
  end if;
  if j.attempts >= p_retries then
    return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(
      p_id, 'retries_exhausted', 'failed', p_reconcile_s)));
  end if;
  -- A prepublication requeue: a NEW generation will be minted by the next claim.
  update infrx.attempts set released_at = p_now, finished_at = coalesce(finished_at, p_now)
   where job_id = p_id and kind = 'inference' and generation = a.generation;
  -- R38: only the unspent remainder of the queue budget, never past deadline_at.
  update infrx.jobs set state = 'queued', attempts = attempts + 1, queued_at = p_now,
                        queue_deadline_at = least(p_now + make_interval(
                          secs => greatest(0, budget_queue_wait_s - queue_wait_used_s)),
                          deadline_at)
   where request_id = p_id
  returning * into j;
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), p_id, j.org_id, 'inference_dispatch',
          jsonb_build_object('job_handle', j.job_handle, 'request_id', p_id,
                             'attempt', j.attempts), p_now)
  returning * into o;
  return jsonb_build_array(jsonb_build_object('index_event', infrx.index_event(o, j)));
end $$;

-- The 24 h window's exit (02): a terminal job's unknown-usage hold is released
-- platform-absorbed on the database clock, never debited.
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
  return jsonb_build_array(jsonb_build_object('outcome',
                                              infrx.job_admission(p_id)->'outcome'));
end $$;

-- Args `{limits, limit}`; answers `[{"outcome": …} | {"index_event": …} |
-- {"unsettleable": {job_id, code, detail}}]`. Bounded by `limit` per pass (and the scan by
-- the active-job cap). Each job runs in its own subtransaction.
create or replace function infrx.recover(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_retries int := (p_args->'limits'->>'max_prepublication_retries')::int;
  v_reconcile float8 := infrx.lease_limit(p_args, 'unknown_usage_reconcile_s');
  v_limit int := greatest(1, least(coalesce((p_args->>'limit')::int, 1000), 10000));
  v_out jsonb := '[]';
  v_id uuid;
begin
  if v_retries is null or v_retries < 0 then
    perform infrx.refuse('invalid_request', 'limits.max_prepublication_retries is required');
  end if;
  for v_id in
    select j.request_id from infrx.jobs j
     where j.state in ('preparing', 'queued', 'running')
       and ((j.state = 'preparing' and j.preparation_deadline_at <= v_now)
         or (j.state = 'queued' and j.queue_deadline_at <= v_now)
         or exists (select 1 from infrx.attempts a
                     where a.job_id = j.request_id and a.released_at is null
                       and (a.expires_at <= v_now or a.generation_deadline_at <= v_now)))
     order by j.deadline_at, j.request_id
     limit v_limit
  loop
    begin
      v_out := v_out || infrx.recover_job(v_id, v_now, v_retries, v_reconcile);
    exception when others then
      v_out := v_out || jsonb_build_array(jsonb_build_object('unsettleable',
        jsonb_build_object('job_id', v_id, 'code', sqlstate, 'detail', sqlerrm)));
    end;
  end loop;
  for v_id in
    select request_id from infrx.jobs
     where settlement_state = 'held_unknown' and reconcile_after <= v_now
     order by reconcile_after, request_id
     limit v_limit
  loop
    begin
      v_out := v_out || infrx.release_aged_unknown(v_id, v_now);
    exception when others then
      v_out := v_out || jsonb_build_array(jsonb_build_object('unsettleable',
        jsonb_build_object('job_id', v_id, 'code', sqlstate, 'detail', sqlerrm)));
    end;
  end loop;
  return v_out;
end $$;

-- ================================================================ privileges ===
do $$
declare
  f text;
begin
  -- Internal: only SECURITY DEFINER bodies call these.
  foreach f in array array[
      'infrx.lease_limit(jsonb, text)',
      'infrx.quarantine_hold_legacy_usd(uuid, timestamptz)',
      'infrx.quarantine_hold_credit(uuid, timestamptz)',
      'infrx.terminalize_no_usage(uuid, text, text, double precision)',
      'infrx.fence_lease(jsonb, text[], double precision)',
      'infrx.recover_job(uuid, timestamptz, integer, double precision)',
      'infrx.release_aged_unknown(uuid, timestamptz)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role',
                   f);
  end loop;
  -- The platform's operations: service_role only. (claim, heartbeat, cancel and
  -- terminalize keep 0004's grants: `create or replace` preserves them.)
  foreach f in array array['infrx.load_work(jsonb)', 'infrx.recover(jsonb)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
end $$;
comment on function infrx.claim(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D3 (0016): queued -> '
  'running, the next inference generation under the job row lock.';
comment on function infrx.heartbeat(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D3 (0016): the fence, '
  'then the stored lease renewed on the database clock.';
comment on function infrx.cancel(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D3 (0016): cancellation '
  'that terminalizes and releases the hold and capacity in one transaction.';
comment on function infrx.terminalize(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; fenced prefix D3 (0016); the '
  'settlement after the fence is D5''s.';
