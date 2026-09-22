-- D2: durable preparation and the dispatch outbox (D-durable-state §D2; 02 §2-§3;
-- 08 §10 R20, R29, R38, R39, R46, R52, R55; oracle DUR-OUTBOX).
--
--   claim_preparation   a fenced preparation lease (R46/R52): own generation counter,
--                       one live lease per job, bounded retries, R29 deadline first.
--   prepare             the 06 boundary D2 owns: `preparing -> queued` fenced on that
--                       lease, prepared refs stored once, preparation unit released,
--                       queue instant derived (R38), `inference_dispatch` written.
--   dispatch_pending    the relay's read: unacknowledged dispatch rows whose job still
--                       wants them, stamped claimed (redelivered after `redelivery_s`);
--                       a row whose job moved on is acknowledged as superseded.
--   acknowledge_dispatch  delivery acknowledgment: the index holds the candidate.
--   dispatch_snapshot   PostgreSQL truth for a Scheduler rebuild (Q2 `rebuild`).
--
-- The queue is never the authority: a candidate only names a job, and
-- `claim_preparation`/`claim` decide who runs it. So a lost acknowledgment (redelivery)
-- and a duplicate delivery (the same event twice, or after a rebuild) can at worst index a
-- candidate twice - never create a second executable attempt.
--
-- Terminalization here is only the one R29 requires on the preparation path
-- (`preparation_failed`, released_free, R39: committed, then refused); the full settling
-- transaction (usage row, terminal journal event) is D5's.
--
-- Additive and re-runnable.

-- ================================================ release a hold, per unit ===
-- R64/R65: one function per unit, so no body reads both money tables.
create or replace function infrx.release_hold_legacy_usd(p_request_id uuid) returns void
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  h infrx.credit_holds%rowtype;
begin
  update infrx.credit_holds set state = 'released', reconcile_after = null,
                                updated_at = infrx.now()
   where request_id = p_request_id and state in ('held', 'unknown')
  returning * into h;
  if found then
    update infrx.wallets set reserved_total = reserved_total - h.amount,
                             revision = revision + 1, updated_at = infrx.now()
     where org_id = h.org_id;
  end if;
end $$;

create or replace function infrx.release_hold_credit(p_request_id uuid) returns void
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  -- The hold trigger moves the wallet's reservation (0006).
  update infrx.credit_wallet_holds set state = 'released', reconcile_after = null
   where request_id = p_request_id and state in ('held', 'unknown');
end $$;

-- A job that never produced output ends here: the hold is released (never debited),
-- every reservation and attempt is released, the tombstone clock starts (its expiry is
-- derived from `settled_at`, 0011), and the two projections are written.
create or replace function infrx.terminalize_unstarted(p_request_id uuid, p_cause text)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
  v_now timestamptz := infrx.now();
  v_settlement text := case when p_cause in ('invalid_media', 'preparation_failed',
                                             'queue_wait_expired')
                            then 'released_free' else 'released_platform_absorbed' end;
begin
  update infrx.jobs set state = case p_cause when 'queue_wait_expired' then 'expired'
                                             else 'failed' end,
                        outcome_cause = p_cause, settlement_state = v_settlement,
                        settled_at = v_now
   where request_id = p_request_id and state in ('preparing', 'queued')
  returning * into j;
  if not found then
    return;
  end if;
  if j.accounting_regime = 'credit' then
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
end $$;

-- The lease document the port returns (`records.Lease`).
create or replace function infrx.lease_doc(a infrx.attempts) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('job_id', a.job_id, 'kind', a.kind, 'generation', a.generation,
    'worker_id', a.worker_id, 'acquired_at', a.acquired_at, 'expires_at', a.expires_at,
    'generation_deadline_at', a.generation_deadline_at,
    'first_token_deadline_at', a.first_token_deadline_at);
$$;

-- ======================================================= claim_preparation ===
-- Returns `{"lease": …}`, or `{"refusal": {code, detail}}` when the refusal follows a
-- terminalization that must commit (R39); plain refusals raise.
create or replace function infrx.claim_preparation(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_worker text := p_args->>'worker_id';
  j infrx.jobs%rowtype;
  a infrx.attempts%rowtype;
begin
  if v_worker is null or length(btrim(v_worker)) = 0 then
    perform infrx.refuse('invalid_request', 'a worker id is required');
  end if;
  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || coalesce(p_args->>'job_id', ''));
  end if;
  if j.settled_at is not null then
    perform infrx.refuse('already_terminal', 'job ' || j.request_id || ' is ' || j.state);
  end if;
  if j.state <> 'preparing' then
    perform infrx.refuse('not_claimable', 'job ' || j.request_id || ' is ' || j.state
                         || ', not preparing');
  end if;
  -- R29: past the preparation instant the job is terminalized HERE, and that commits.
  if v_now >= j.preparation_deadline_at then
    perform infrx.terminalize_unstarted(j.request_id, 'preparation_failed');
    return jsonb_build_object('refusal', jsonb_build_object('code', 'already_terminal',
      'detail', 'job ' || j.request_id || ' passed its preparation deadline'));
  end if;
  select * into a from infrx.attempts
   where job_id = j.request_id and kind = 'preparation' and released_at is null;
  if found and v_now < a.expires_at then
    perform infrx.refuse('not_claimable', 'job ' || j.request_id
                         || ' is already being prepared by ' || a.worker_id);
  end if;
  if found then                          -- an expired, unreaped lease: superseded now
    update infrx.attempts set released_at = v_now
     where job_id = a.job_id and kind = a.kind and generation = a.generation;
  end if;
  -- R46: the first claim plus MAX_PREPUBLICATION_RETRIES further ones.
  if j.preparation_attempts > (p_args->'limits'->>'max_prepublication_retries')::int then
    perform infrx.terminalize_unstarted(j.request_id, 'preparation_failed');
    return jsonb_build_object('refusal', jsonb_build_object('code', 'not_claimable',
      'detail', 'job ' || j.request_id || ' exhausted its preparation retries'));
  end if;
  update infrx.jobs set preparation_attempts = preparation_attempts + 1
   where request_id = j.request_id;
  -- R52: a short lease, never past the phase it fences.
  insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at, expires_at,
                              generation_deadline_at)
  values (j.request_id, 'preparation', j.preparation_attempts + 1, v_worker, v_now,
          least(v_now + make_interval(
            secs => (p_args->'limits'->>'preparation_lease_ttl_s')::float8),
            j.preparation_deadline_at),
          j.preparation_deadline_at)
  returning * into a;
  return jsonb_build_object('lease', infrx.lease_doc(a));
end $$;

-- ================================================ prepare: preparing -> queued ===
-- The fence, in the fake's order: kind, job, terminal, live lease, generation, owner,
-- then R29's phase deadline BEFORE expiry (R55: a clamped lease expires with the phase,
-- so expiry-first would leave the job preparing with its hold reserved), then expiry.
create or replace function infrx.prepare(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  l jsonb := p_args->'lease';
  j infrx.jobs%rowtype;
  a infrx.attempts%rowtype;
  v_remaining float8;
begin
  if jsonb_typeof(l) is distinct from 'object'
     or jsonb_typeof(coalesce(p_args->'media', '[]')) <> 'array' then
    perform infrx.refuse('invalid_request', 'prepare takes {lease, media}');
  end if;
  if l->>'kind' is distinct from 'preparation' then
    perform infrx.refuse('stale_lease', coalesce(l->>'kind', 'no') ||
                         ' lease cannot fence preparation');
  end if;
  select * into j from infrx.jobs where request_id = (l->>'job_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || (l->>'job_id'));
  end if;
  if j.settled_at is not null then
    perform infrx.refuse('already_terminal', 'job ' || j.request_id || ' is already '
                         || j.state);
  end if;
  select * into a from infrx.attempts
   where job_id = j.request_id and kind = 'preparation' and released_at is null;
  if j.state <> 'preparing' or not found then
    perform infrx.refuse('stale_lease', 'job ' || j.request_id || ' is ' || j.state
                         || ' with no preparation lease');
  end if;
  if a.generation is distinct from (l->>'generation')::int then
    perform infrx.refuse('stale_lease', 'preparation generation ' || (l->>'generation')
                         || ' != ' || a.generation);
  end if;
  if a.worker_id is distinct from l->>'worker_id' then
    perform infrx.refuse('stale_lease', 'preparation lease belongs to ' || a.worker_id);
  end if;
  if v_now >= j.preparation_deadline_at then
    perform infrx.terminalize_unstarted(j.request_id, 'preparation_failed');
    return jsonb_build_object('refusal', jsonb_build_object('code', 'already_terminal',
      'detail', 'job ' || j.request_id || ' passed its preparation deadline'));
  end if;
  if v_now >= a.expires_at then
    perform infrx.refuse('stale_lease', 'preparation lease expired at ' || a.expires_at);
  end if;
  -- R10/R55: prepared refs are the job's own tenant's, checked at the write.
  if exists (select 1 from jsonb_array_elements(coalesce(p_args->'media', '[]')) m
             where (m->>'org_id')::uuid is distinct from j.org_id) then
    perform infrx.refuse('forbidden', 'prepared media must belong to the job''s org');
  end if;
  update infrx.attempts set released_at = v_now, finished_at = v_now
   where job_id = a.job_id and kind = a.kind and generation = a.generation;
  update infrx.capacity_reservations set active = false, released_at = v_now
   where request_id = j.request_id and kind = 'preparation' and active;
  -- R38: the queue instant is what is left of the budget, never past deadline_at.
  v_remaining := greatest(0, j.budget_queue_wait_s - j.queue_wait_used_s);
  update infrx.jobs set state = 'queued', queued_at = v_now,
                        prepared_refs = coalesce(p_args->'media', '[]'),
                        queue_deadline_at = least(v_now + make_interval(secs => v_remaining),
                                                  j.deadline_at)
   where request_id = j.request_id;
  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)
  values (gen_random_uuid(), j.request_id, j.org_id, 'inference_dispatch',
          jsonb_build_object('job_handle', j.job_handle, 'request_id', j.request_id), v_now);
  return infrx.job_admission(j.request_id);
end $$;

-- ================================================================ the relay ===
-- One IndexEvent per row (`records.IndexEvent`).
create or replace function infrx.index_event(o infrx.outbox, j infrx.jobs) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('event_id', o.event_id, 'job_id', j.request_id,
    'org_id', j.org_id, 'key_id', j.key_id, 'kind', o.kind,
    'execution_mode', j.execution_mode, 'available_at', o.available_at,
    'attempt', case o.kind when 'prepare_dispatch' then j.preparation_attempts
                           else j.attempts end);
$$;

-- Whether a dispatch row still names work its job wants.
create or replace function infrx.dispatch_wanted(p_kind text, p_state text) returns boolean
language sql immutable set search_path = infrx, public, pg_temp as $$
  select (p_kind = 'prepare_dispatch' and p_state = 'preparing')
      or (p_kind = 'inference_dispatch' and p_state = 'queued');
$$;

-- Args `{limit, worker_id, redelivery_s}`. At-least-once: a row stays pending until
-- `acknowledge_dispatch`; a claimed row is handed out again once `redelivery_s` passed
-- without an acknowledgment (the relay died between the index write and the ack).
create or replace function infrx.dispatch_pending(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_out jsonb := '[]';
  r record;
begin
  for r in
    select o as ev, j as job from infrx.outbox o
      join infrx.jobs j on j.request_id = o.aggregate_id
     where o.acknowledged_at is null and o.available_at <= v_now
       and o.kind in ('prepare_dispatch', 'inference_dispatch')
       and (o.claimed_at is null or o.claimed_at <= v_now - make_interval(
              secs => coalesce((p_args->>'redelivery_s')::float8, 30)))
     order by o.available_at, o.event_id
     limit greatest(1, least(coalesce((p_args->>'limit')::int, 100), 1000))
     for update of o skip locked
  loop
    if infrx.dispatch_wanted((r.ev).kind, (r.job).state) then
      update infrx.outbox set claimed_at = v_now, claimed_by = p_args->>'worker_id',
                              attempts = attempts + 1
       where event_id = (r.ev).event_id;
      v_out := v_out || jsonb_build_array(infrx.index_event(r.ev, r.job));
    else
      -- The job moved on (queued, running, terminal): nothing is left to dispatch.
      update infrx.outbox set acknowledged_at = v_now, last_error = 'superseded'
       where event_id = (r.ev).event_id;
    end if;
  end loop;
  return v_out;
end $$;

-- Args `{event_ids: [...]}`; returns how many were newly acknowledged. Idempotent.
create or replace function infrx.acknowledge_dispatch(p_args jsonb) returns int
language sql security definer set search_path = infrx, public, pg_temp as $$
  with acked as (
    update infrx.outbox set acknowledged_at = infrx.now()
     where event_id in (select (value #>> '{}')::uuid
                        from jsonb_array_elements(coalesce(p_args->'event_ids', '[]')))
       and kind in ('prepare_dispatch', 'inference_dispatch')
       and acknowledged_at is null
    returning 1)
  select count(*)::int from acked;
$$;

-- The rebuild source: every job that wants a dispatch now, with its LATEST dispatch
-- event (stable ids, so a rebuild followed by a redelivery indexes one candidate). A
-- preparing job whose preparation lease is live is being worked, so it is not a candidate.
create or replace function infrx.dispatch_snapshot() returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(infrx.index_event(o, j) order by o.available_at, o.event_id),
                  '[]')
  from infrx.jobs j
  join lateral (select * from infrx.outbox x
                 where x.aggregate_id = j.request_id
                   and infrx.dispatch_wanted(x.kind, j.state)
                 order by x.created_at desc, x.event_id desc limit 1) o on true
  where j.state in ('preparing', 'queued')
    and not exists (select 1 from infrx.attempts a
                     where a.job_id = j.request_id and a.kind = 'preparation'
                       and a.released_at is null and a.expires_at > infrx.now());
$$;

-- ================================================================ privileges ===
do $$
declare
  f text;
begin
  -- Internal: only SECURITY DEFINER bodies call these.
  foreach f in array array[
      'infrx.release_hold_legacy_usd(uuid)', 'infrx.release_hold_credit(uuid)',
      'infrx.terminalize_unstarted(uuid, text)', 'infrx.lease_doc(infrx.attempts)',
      'infrx.index_event(infrx.outbox, infrx.jobs)', 'infrx.dispatch_wanted(text, text)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role',
                   f);
  end loop;
  -- The platform's operations: service_role only.
  foreach f in array array[
      'infrx.claim_preparation(jsonb)', 'infrx.dispatch_pending(jsonb)',
      'infrx.acknowledge_dispatch(jsonb)', 'infrx.dispatch_snapshot()']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
end $$;
comment on function infrx.prepare(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D2 (0012): the fenced '
  'preparing -> queued transition with its inference_dispatch outbox event.';
