-- D4: the persistent stream journal and replay (D-durable-state §D4; 02 §5-§7 and
-- "Output and unknown outcomes"; 08 §10 R7, R10, R20, R25, R29, R30, R36, R39, R46, R59,
-- R78, R84; oracles DUR-OUTPUT, DUR-FENCE, DUR-CAP, API-STREAM).
--
--   append           the 06 boundary (signature and grants D1's, body here). The fence
--                    FIRST (`infrx.fence_lease`, inference leases only - R46): it locks the
--                    job row, and an R29 terminalization it makes is committed and answered
--                    as a refusal (R39). Then the batch: no terminal event anywhere (R30);
--                    every size measured HERE (R25: one event over the limit refuses the
--                    whole batch - nothing stored, not a prefix); the per-job ceiling that
--                    holds back the terminal event's reserve; sequences continuing the
--                    generation; the database clock and the store's TTL; the stored bytes
--                    and `jobs.published` in the same transaction. The committed rows are
--                    the answer, and the caller only receives them after COMMIT (the answer
--                    IS the subscriber notification: nothing is relayed before it).
--   journal_terminal_event  the trigger that writes ONE terminal event for every
--                    terminalization - 0012's preparation failure, 0016's cancel, R29 inside
--                    the fence and every `recover_job` cause, and D5's settlement - from the
--                    STORED row (R30), last in the journal, in the terminalizing transaction.
--                    `held_unknown -> released_platform_absorbed` (the 24 h release) leaves
--                    `settled_at` alone and writes nothing (0003's one-terminal index).
--   read_journal     tenant-bound (org, handle) bounded replay in (generation, sequence)
--                    order; typed `invalid_cursor` past the head, `replay_gap` below the
--                    prune watermark, `journal_expired` when nothing is left.
--   expire_journal   pruning on the database clock (a caller's `now` is a bound at most,
--                    R7), a prefix per job, the watermark persisted, bytes freed once.
--   journal_usage    reserved / stored / charged bytes and the chunk count: the journal
--                    readiness probe's body (G1R request 1).
--
-- DUR-CAP WITHOUT A GLOBAL LOCK. The global charge is, per job, max(live reservation,
-- stored bytes) (0011 `journal_bytes_charged`). An append may fill its job's reservation
-- except for the bytes held back for the terminal event (least(1024, reservation/2), the
-- fake's TERMINAL_EVENT_RESERVE_BYTES), so `stored` stays below the reservation and no
-- append can raise the charge; the terminal event (< 1024 bytes for every state, cause and
-- settlement) then fits the held-back bytes, and terminalization turns the job's charge
-- from its reservation into its smaller stored bytes, which keep counting until pruned.
--
-- LOCK ORDER (continues 0011's and 0016's). `append` takes ONE job row (through the fence)
-- and nothing else - never `pg_advisory_xact_lock(infrx.admission_lock_key())`: admission
-- takes that scope lock FIRST, so taking it after a job row would invert 0011's order (and
-- the argument above makes it unnecessary). The terminal event is written by a trigger in
-- the terminalizing transaction, under the job row lock that transaction already holds.
-- `expire_journal` takes job rows SKIP LOCKED, one at a time, and holds nothing else, so it
-- never waits on an append, a cancel or a settlement in flight.
--
-- EXPIRED is not a flag: a journal is expired when it has a prune watermark and no chunk
-- left. The watermark (`jobs.journal_pruned_generation/_sequence`, columns `jobs_guard`
-- does not freeze) is the highest pruned cursor; pruning removes a PREFIX, so everything
-- above it is present and everything at or below it is an explicit gap.
--
-- ROLLBACK (0017 alone; nothing before it is edited): `drop trigger
-- jobs_terminal_journal_event on infrx.jobs`; drop functions `infrx.journal_terminal_event()`,
-- `infrx.chunk_doc(infrx.stream_chunks)`, `infrx.read_journal(jsonb)`,
-- `infrx.expire_journal(jsonb)`, `infrx.journal_usage()`; `alter table infrx.jobs drop column
-- journal_pruned_generation, drop column journal_pruned_sequence`; and restore the `append`
-- stub exactly as 0004's boundary block writes it (`perform infrx.unimplemented('append',
-- 'D4'); return null;`, `create or replace` keeps its grants). Stored chunks and
-- `journal_stored_bytes` are 0003's and are left as they are.
--
-- Additive and re-runnable.

-- ============================================================ the watermark ===
alter table infrx.jobs
  add column if not exists journal_pruned_generation int,
  add column if not exists journal_pruned_sequence int;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.jobs'::regclass
                 and conname = 'jobs_journal_watermark_is_one_cursor') then
    alter table infrx.jobs add constraint jobs_journal_watermark_is_one_cursor
      check ((journal_pruned_generation is null) = (journal_pruned_sequence is null)
             and coalesce(journal_pruned_generation >= 1 and journal_pruned_sequence >= 1,
                          true));
  end if;
end $$;

-- A chunk as the port returns it (`records.Chunk`).
create or replace function infrx.chunk_doc(c infrx.stream_chunks) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('job_id', c.job_id, 'generation', c.generation,
    'sequence', c.sequence, 'event_type', c.event_type, 'payload', c.payload,
    'bytes', c.bytes, 'persisted_at', c.committed_at, 'expires_at', c.expires_at);
$$;

-- ================================================================= append ===
-- Args `{lease, events: [{type, payload}], limits}`; answers `{"chunks": [...]}` (the
-- committed rows) or `{"refusal": …}` after an R29 terminalization (R39).
create or replace function infrx.append(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_lease jsonb := p_args->'lease';
  v_events jsonb := p_args->'events';
  v_max_event float8;
  v_ttl float8;
  v_refusal jsonb;
  v_count int;
  v_bytes bigint;
  v_largest bigint;
  v_next int;
  v_chunks jsonb;
  j infrx.jobs%rowtype;
begin
  if jsonb_typeof(v_lease) is distinct from 'object'
     or jsonb_typeof(v_events) is distinct from 'array' then
    perform infrx.refuse('invalid_request', 'append takes {lease, events, limits}');
  end if;
  v_max_event := infrx.lease_limit(p_args, 'journal_event_max_bytes');
  v_ttl := infrx.lease_limit(p_args, 'journal_chunk_ttl_s');
  -- The fence FIRST: it locks the job row, and past R29's instant it has terminalized the
  -- job (writing its terminal event) and answers a refusal that must commit (R39).
  v_refusal := infrx.fence_lease(v_lease, array['inference'],
                                 infrx.lease_limit(p_args, 'unknown_usage_reconcile_s'));
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  select * into j from infrx.jobs where request_id = (v_lease->>'job_id')::uuid;
  -- Under the row lock the fence holds: the generation's next sequence, never at or below
  -- the prune watermark (a fully pruned journal must not reissue a pruned cursor).
  select greatest(coalesce(max(c.sequence), 0),
                  case when j.journal_pruned_generation = (v_lease->>'generation')::int
                       then j.journal_pruned_sequence else 0 end)
    into v_next from infrx.stream_chunks c
   where c.job_id = j.request_id and c.generation = (v_lease->>'generation')::int;
  -- R30: a worker never appends a terminal event, anywhere in the batch - the terminal
  -- event is derived from the stored outcome by the trigger below. Only events are stored.
  if exists (select 1 from jsonb_array_elements(v_events) with ordinality as e(event, n)
              where not coalesce(e.event->>'type' in ('progress', 'delta', 'usage', 'error')
                                 and jsonb_typeof(e.event->'payload') = 'object', false)) then
    perform infrx.refuse('invalid_request', 'a worker may append only progress, delta, '
                         'usage and error events with an object payload (R30)');
  end if;
  -- R25: every size is measured here, on the text the row stores (0003 binds `bytes` to
  -- it); a caller's count is never trusted. One event over the limit refuses the WHOLE
  -- batch: nothing is stored, not a prefix.
  select count(*), coalesce(sum(octet_length((e->'payload')::text)), 0),
         coalesce(max(octet_length((e->'payload')::text)), 0)
    into v_count, v_bytes, v_largest
    from jsonb_array_elements(v_events) e;
  if v_largest > v_max_event then
    perform infrx.refuse('journal_write_failed', 'an event of ' || v_largest
                         || ' bytes exceeds journal_event_max_bytes ' || v_max_event);
  end if;
  -- An empty batch commits nothing, so it must not forbid a prepublication requeue.
  if v_count = 0 then
    return jsonb_build_object('chunks', '[]'::jsonb);
  end if;
  -- DUR-CAP: the reservation minus the bytes held back for the terminal event (header).
  if j.journal_stored_bytes + v_bytes
       > j.journal_reserved_bytes - least(1024, j.journal_reserved_bytes / 2) then
    perform infrx.refuse('journal_capacity_exhausted', 'job ' || j.request_id
                         || ' would store ' || (j.journal_stored_bytes + v_bytes)
                         || ' bytes, past what its journal reservation leaves an append', 30);
  end if;
  with written as (
    insert into infrx.stream_chunks as c (job_id, generation, sequence, event_type, payload,
                                          bytes, committed_at, expires_at)
    select j.request_id, (v_lease->>'generation')::int, v_next + e.n, e.event->>'type',
           e.event->'payload', octet_length((e.event->'payload')::text), v_now,
           v_now + make_interval(secs => v_ttl)
      from jsonb_array_elements(v_events) with ordinality as e(event, n)
    returning c.sequence, infrx.chunk_doc(c) as doc)
  select jsonb_agg(doc order by sequence) into v_chunks from written;
  -- The first committed chunk is the publication marker (02 §6), in this transaction.
  update infrx.jobs set journal_stored_bytes = journal_stored_bytes + v_bytes,
                        published = true
   where request_id = j.request_id;
  return jsonb_build_object('chunks', v_chunks);
end $$;

-- ==================================================== the terminal event ===
-- R30: derived from the STORED row, once (settled_at goes NULL -> value exactly once;
-- `jobs_guard` freezes it after), after the newest chunk. A job that never appended writes
-- at its last inference generation (else 1), just past a prune watermark if one exists,
-- with the default JOURNAL_CHUNK_TTL_S (08 §5); otherwise with its newest chunk's TTL, so a
-- job's chunks expire in cursor order. Its bytes are counted like any chunk's, so pruning it
-- later frees exactly what was added. The settling write may use the held-back bytes and
-- is refused (the whole terminalization with it) only if even they cannot fit the WIDEST
-- terminal payload any state, cause and settlement makes (107 bytes; the fake's
-- `check_terminal_capacity`, R39), which never happens at production sizes (1024 held back).
-- A job with NO journal reservation has no journal (an append can store nothing in it
-- either): every admitted job has one (0011), so only D1's raw fixture rows are skipped.
create or replace function infrx.journal_terminal_event() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_payload jsonb := jsonb_build_object('state', new.state, 'cause', new.outcome_cause,
                                        'settlement_state', new.settlement_state);
  v_bytes int := octet_length(v_payload::text);
  -- The widest `{cause, state, settlement_state}` text over the 0003 vocabularies
  -- (tests/d/checks_journal computes it and pins it at the boundary).
  v_widest constant int := 107;
  v_now timestamptz := infrx.now();
  v_generation int;
  v_sequence int;
  v_ttl interval;
begin
  if new.journal_stored_bytes + v_widest > new.journal_reserved_bytes then
    perform infrx.refuse('journal_capacity_exhausted', 'job ' || new.request_id
                         || ' has no journal room for its terminal event', 30);
  end if;
  select c.generation, c.sequence + 1, c.expires_at - c.committed_at
    into v_generation, v_sequence, v_ttl
    from infrx.stream_chunks c where c.job_id = new.request_id
   order by c.generation desc, c.sequence desc limit 1;
  if not found then
    select greatest(coalesce(max(a.generation), 1), coalesce(new.journal_pruned_generation, 0))
      into v_generation
      from infrx.attempts a where a.job_id = new.request_id and a.kind = 'inference';
    v_sequence := case when v_generation = new.journal_pruned_generation
                       then new.journal_pruned_sequence + 1 else 1 end;
    v_ttl := interval '3600 seconds';
  end if;
  insert into infrx.stream_chunks (job_id, generation, sequence, event_type, payload, bytes,
                                   committed_at, expires_at)
  values (new.request_id, v_generation, v_sequence, 'terminal', v_payload, v_bytes, v_now,
          v_now + v_ttl);
  update infrx.jobs set journal_stored_bytes = journal_stored_bytes + v_bytes
   where request_id = new.request_id;
  return null;
end $$;
create or replace trigger jobs_terminal_journal_event after update of settled_at on infrx.jobs
  for each row when (old.settled_at is null and new.settled_at is not null
                    and new.journal_reserved_bytes > 0)
  execute function infrx.journal_terminal_event();

-- ================================================================= replay ===
-- Args `{org_id, job_handle, cursor: {generation, sequence} | null, limit}`; answers
-- `{"chunks": [...]}`, at most 1000 (the fake's MAX_READ_LIMIT), in cursor order. R10:
-- ownership is the WHERE clause - another tenant's handle and an unknown one are the same
-- `not_found`. The cursor is a client's (`Last-Event-ID`), so its parts are read as numeric:
-- any magnitude is a typed answer (past the head), never an integer overflow.
create or replace function infrx.read_journal(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_limit numeric := (p_args->>'limit')::numeric;
  v_position_generation numeric := coalesce((p_args->'cursor'->>'generation')::numeric, 0);
  v_position_sequence numeric := coalesce((p_args->'cursor'->>'sequence')::numeric, 0);
  v_head_generation int := 0;
  v_head_sequence int := 0;
  v_chunks jsonb;
  j infrx.jobs%rowtype;
begin
  select * into j from infrx.jobs
   where org_id = (p_args->>'org_id')::uuid and job_handle = p_args->>'job_handle';
  if not found then
    perform infrx.refuse('not_found', 'no job ' || coalesce(p_args->>'job_handle', '')
                         || ' owned by org ' || coalesce(p_args->>'org_id', ''));
  end if;
  if v_limit is null or v_limit <= 0 then
    perform infrx.refuse('invalid_request', 'limit must be a positive integer');
  end if;
  select c.generation, c.sequence into v_head_generation, v_head_sequence
    from infrx.stream_chunks c where c.job_id = j.request_id
   order by c.generation desc, c.sequence desc limit 1;
  if not found and j.journal_pruned_generation is not null then
    perform infrx.refuse('journal_expired', 'the journal of job ' || j.job_handle
                         || ' has expired; its status remains');
  end if;
  -- A cursor the journal never issued is a client bug, not an empty page to poll for ever.
  if (v_position_generation, v_position_sequence)
       > (coalesce(v_head_generation, 0), coalesce(v_head_sequence, 0)) then
    perform infrx.refuse('invalid_cursor', 'cursor ' || v_position_generation || '-'
                         || v_position_sequence || ' is past the last event');
  end if;
  if (v_position_generation, v_position_sequence)
       < (j.journal_pruned_generation, j.journal_pruned_sequence) then
    perform infrx.refuse('replay_gap', 'events up to ' || j.journal_pruned_generation || '-'
                         || j.journal_pruned_sequence || ' are no longer retained');
  end if;
  select coalesce(jsonb_agg(p.doc order by p.generation, p.sequence), '[]') into v_chunks
    from (select c.generation, c.sequence, infrx.chunk_doc(c) as doc
            from infrx.stream_chunks c
           where c.job_id = j.request_id
             and (c.generation, c.sequence) > (v_position_generation, v_position_sequence)
           order by c.generation, c.sequence
           limit least(v_limit, 1000)) p;
  return jsonb_build_object('chunks', v_chunks);
end $$;

-- ================================================================== prune ===
-- Args `{now: timestamptz | null, limit}`; answers the number of chunks removed. R7: a
-- caller's `now` is a bound at most, never ahead of the database clock.
create or replace function infrx.expire_journal(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_db_now timestamptz := infrx.now();
  v_bound timestamptz := least(coalesce((p_args->>'now')::timestamptz, v_db_now), v_db_now);
  v_limit int := greatest(1, least(coalesce((p_args->>'limit')::int, 1000), 10000));
  v_removed int := 0;
  v_job uuid;
  v_generation int;
  v_sequence int;
  v_count int;
  v_bytes bigint;
begin
  -- ponytail: bounded by jobs per pass, not chunks; a chunk bound if one journal's chunk
  -- count ever makes a single prefix delete too long.
  for v_job in
    select c.job_id from infrx.stream_chunks c where c.expires_at <= v_bound
     group by c.job_id order by min(c.expires_at), c.job_id limit v_limit
  loop
    -- SKIP LOCKED: a job whose append, cancel or settlement is in flight is the next
    -- pass's; this sweep never waits on a job row.
    perform 1 from infrx.jobs where request_id = v_job for update skip locked;
    if not found then
      continue;
    end if;
    select c.generation, c.sequence into v_generation, v_sequence
      from infrx.stream_chunks c where c.job_id = v_job and c.expires_at <= v_bound
     order by c.generation desc, c.sequence desc limit 1;
    if not found then
      continue;
    end if;
    -- A prefix: everything at or below the newest expired chunk, so the watermark only
    -- grows and nothing above it is ever missing.
    with gone as (delete from infrx.stream_chunks
                   where job_id = v_job and (generation, sequence) <= (v_generation, v_sequence)
                  returning bytes)
    select count(*), coalesce(sum(bytes), 0) into v_count, v_bytes from gone;
    update infrx.jobs set journal_stored_bytes = journal_stored_bytes - v_bytes,
                          journal_pruned_generation = v_generation,
                          journal_pruned_sequence = v_sequence
     where request_id = v_job;
    v_removed := v_removed + v_count;
  end loop;
  return to_jsonb(v_removed);
end $$;

-- ================================================================== usage ===
-- The journal's accounting now, and the readiness probe's body (G1R request 1).
-- ponytail: `count(*)` of the chunks per call; pg_class.reltuples if the probe is hot.
create or replace function infrx.journal_usage() returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'reserved_bytes', (select coalesce(sum(r.amount), 0) from infrx.capacity_reservations r
                        where r.kind = 'journal_bytes' and r.active),
    'stored_bytes', (select coalesce(sum(j.journal_stored_bytes), 0) from infrx.jobs j
                      where j.journal_stored_bytes > 0),
    'charged_bytes', infrx.journal_bytes_charged(),
    'chunks', (select count(*) from infrx.stream_chunks));
$$;

-- ============================================================= privileges ===
-- `append` keeps 0004's grant (service_role only): `create or replace` preserves it.
revoke all on function infrx.read_journal(jsonb) from public, anon, authenticated;
grant execute on function infrx.read_journal(jsonb) to service_role;
revoke all on function infrx.expire_journal(jsonb) from public, anon, authenticated;
grant execute on function infrx.expire_journal(jsonb) to service_role;
revoke all on function infrx.journal_usage() from public, anon, authenticated;
grant execute on function infrx.journal_usage() to service_role;
-- Internal: only the SECURITY DEFINER bodies (and the trigger) use these.
revoke all on function infrx.journal_terminal_event()
  from public, anon, authenticated, service_role;
revoke all on function infrx.chunk_doc(infrx.stream_chunks)
  from public, anon, authenticated, service_role;
comment on function infrx.append(jsonb) is
  'Mutation boundary (06). Signature and grants owned by D1; body D4 (0017): the fence, '
  'then the batch, the stored bytes and the publication marker in one transaction.';
