-- D10.b: the durable content lifecycle - what may be deleted, by whom, and the scrub of
-- content-bearing rows (consumer-v1/01 §D10.b; RV-03, RV-11; F2C.a/F2C.b
-- `contracts/v2/lifecycle.py` ContentLifecycle and ReadOutcome; 08 §10 R7, R10, R30).
--
--   content_objects (0019, extended)  a leased, fenced deletion claim per generation
--                                     (`claim_*`; a claim of another generation is none).
--   content_referenced                THE liveness rule, read under the content row lock:
--                                     a manifest reference of a job that runs or is inside
--                                     its retention; the object's own job likewise (a result
--                                     exactly until its persisted `result_expires_at`); an
--                                     open upload's destination; a finalized, unexpired
--                                     ticket's source; and - for what the previous runtime
--                                     staged without rows - any running job naming the key.
--   content_candidates / content_claim / content_tombstone / content_acknowledge_delete
--                                     the collector's protocol (M6 drives it). PostgreSQL
--                                     decides; an object listing, its age or a process's
--                                     memory never does, and every refusal RETAINS.
--   database content                  `job_results/<id>` (a result body) and `jobs/<id>` (an
--                                     admitted request record) are registered by triggers
--                                     when written; their "delete" is a SCRUB performed by
--                                     `content_acknowledge_delete` in its own transaction -
--                                     the body is emptied, the row, its digest and size, the
--                                     outcome, usage, settlement, ledger and idempotency
--                                     tombstone stay.
--   read_result (0014's)              persisted expiry is the authority (RV-11): from
--                                     `jobs.result_expires_at` on - or with none persisted -
--                                     a result is `result_expired` (410), scrubbed or not;
--                                     never empty text, never regenerated.
--
-- LOCK ORDER (continues 0019's). `claim` and `tombstone` take the content row only; the
-- reference recheck READS jobs, manifests and tickets. `acknowledge_delete` of database
-- content takes the content row, then the job row (the scrub) - a settlement never takes a
-- content row, so no cycle. Admission holds the content row FOR SHARE while it binds, so a
-- tombstone either commits first (admission: `content_retiring`) or sees the reference.
--
-- CLAIMS AND THE EXTERNAL DELETE (F2C.a D2). A claim is a lease of `claim_ttl_s` (the
-- adapter's configuration, which must exceed the object store's request timeout); the
-- collector issues the delete only while its claim has at least one request timeout left,
-- then acknowledges. A lapsed claim is superseded by the next fence; the superseded holder
-- can neither tombstone nor acknowledge. The residual risk is a process pause between that
-- check and the store receiving the delete - the lease's ordinary limit, recorded in D10's
-- evidence (upgrade: per-generation object keys or a conditional delete).
--
-- ROLLBACK (0020 alone). Re-run 0014's `read_result`, point `job_results_immutable` back at
-- `infrx.forbid_update_delete()` (`create or replace trigger`),
-- 0011's `jobs_admission_record_guard`; drop the triggers and functions this file adds and
-- the columns `content_objects.claim_*`, `job_results.scrubbed_at`, `jobs.content_scrubbed_at`
-- - after which a scrubbed row stays scrubbed (its content is gone by design). No money,
-- outcome or idempotency row is touched by this file.
--
-- Additive and re-runnable.

-- ============================================================ deletion claims ===
alter table infrx.content_objects
  add column if not exists claim_generation int,
  add column if not exists claim_fence int not null default 0 check (claim_fence >= 0),
  add column if not exists claim_holder text check (length(claim_holder) between 1 and 200),
  add column if not exists claim_claimed_at timestamptz,
  add column if not exists claim_expires_at timestamptz;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.content_objects'::regclass
                 and conname = 'content_objects_claim_is_one_fact') then
    alter table infrx.content_objects add constraint content_objects_claim_is_one_fact check (
      num_nonnulls(claim_generation, claim_holder, claim_claimed_at, claim_expires_at) in (0, 4)
      and (claim_generation is null
           or (claim_generation <= generation and claim_fence >= 1
               and claim_expires_at > claim_claimed_at)));
  end if;
end $$;

create or replace function infrx.claim_doc(c infrx.content_objects) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('content_id', c.content_id, 'generation', c.claim_generation,
    'fence', c.claim_fence, 'holder', c.claim_holder, 'claimed_at', c.claim_claimed_at,
    'expires_at', c.claim_expires_at);
$$;

-- 0019's document plus the claim, which exists only for the current generation.
create or replace function infrx.content_doc(c infrx.content_objects) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'content_id', c.content_id, 'generation', c.generation,
    'identity', jsonb_build_object('org_id', c.org_id, 'kind', c.kind,
      'location', c.location, 'object_key', c.object_key, 'digest', c.digest,
      'bytes', c.bytes, 'job_id', c.job_id, 'upload_handle', c.upload_handle,
      'origin', c.origin),
    'state', c.state, 'registered_at', c.registered_at, 'eligible_at', c.eligible_at,
    'tombstoned_at', c.tombstoned_at, 'deleted_at', c.deleted_at,
    'claim', case when c.claim_generation = c.generation then infrx.claim_doc(c) end);
$$;

-- ============================================================ the liveness rule ===
-- Why the object must stay at `p_now`, or NULL. Reads only.
create or replace function infrx.content_referenced(c infrx.content_objects, p_now timestamptz)
returns text language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select case
    when exists (
        select 1 from infrx.job_media m
          join infrx.jobs j on j.request_id = m.job_id and j.org_id = m.org_id
          left join infrx.job_readiness r on r.job_id = m.job_id
         where m.content_id = c.content_id and m.generation = c.generation
           and (j.settled_at is null
                or p_now < j.settled_at + make_interval(secs => coalesce(r.retention_s, 0))))
      then 'a job this object is a source of runs or is inside its retention'
    when c.job_id is not null and exists (
        select 1 from infrx.jobs j left join infrx.job_readiness r on r.job_id = j.request_id
         where j.request_id = c.job_id and j.org_id = c.org_id
           and (j.settled_at is null or p_now < case c.kind
                  when 'result' then coalesce(j.result_expires_at, j.settled_at)
                  else j.settled_at + make_interval(secs => coalesce(r.retention_s, 0)) end))
      then 'its own job runs or is inside its retention'
    when c.kind = 'upload_destination' and exists (
        select 1 from infrx.media_uploads u
         where u.org_id = c.org_id and u.handle = c.upload_handle and u.state = 'created'
           and p_now < u.expires_at)
      then 'the destination of an open upload'
    when exists (
        select 1 from infrx.media_uploads u
         where u.source_content_id = c.content_id and u.source_generation = c.generation
           and u.state = 'finalized' and p_now < u.expires_at)
      then 'the source of a usable upload'
    when c.location = 'object_store' and exists (
        select 1 from infrx.jobs j
         where j.org_id = c.org_id and j.state in ('preparing', 'queued', 'running')
           and (j.payload_ref = c.object_key
                or exists (select 1 from jsonb_array_elements(case jsonb_typeof(
                             j.request_record->'media') when 'array'
                             then j.request_record->'media' else '[]' end) e
                            where e->>'storage_ref' = c.object_key)
                or exists (select 1 from jsonb_array_elements(coalesce(j.prepared_refs, '[]')) e
                            where e->>'storage_ref' = c.object_key)))
      then 'a running job names this key'
  end;
$$;

-- ============================================================ the protocol ===
-- `candidates`: live objects past `eligible_at`, unreferenced, with no unexpired claim, and
-- tombstoned ones whose claim lapsed (an unfinished delete), keyset-paged by
-- `(eligible_at, content_id)`. Args `{after_eligible_at, after_content_id, limit}` (the
-- adapter's opaque cursor, decoded). ponytail: the reference rule is evaluated per row of
-- the ordered scan; a partial index of unreferenced rows if referenced ones ever dominate.
create or replace function infrx.content_candidates(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_limit int := (p_args->>'limit')::int;
  v_docs jsonb[];
begin
  if v_limit is null or v_limit < 1 then
    perform infrx.refuse('invalid_request', 'limit is a positive integer');
  end if;
  v_limit := least(v_limit, 1000);
  select array_agg(infrx.content_doc(c) order by c.eligible_at, c.content_id) into v_docs
    from (select * from infrx.content_objects c
           where c.state <> 'deleted' and c.eligible_at <= v_now
             and (p_args->>'after_eligible_at' is null
                  or (c.eligible_at, c.content_id) > ((p_args->>'after_eligible_at')::timestamptz,
                                                      (p_args->>'after_content_id')::uuid))
             and (c.claim_generation is distinct from c.generation or v_now >= c.claim_expires_at)
             and (c.state = 'tombstoned' or infrx.content_referenced(c, v_now) is null)
           order by c.eligible_at, c.content_id
           limit v_limit + 1) c;
  return jsonb_build_object('items', coalesce(to_jsonb(v_docs[1:v_limit]), '[]'),
                            'more', coalesce(array_length(v_docs, 1), 0) > v_limit);
end $$;

-- The row, locked; an unknown id is `not_found`.
create or replace function infrx.content_row(p_id uuid) returns infrx.content_objects
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  c infrx.content_objects%rowtype;
begin
  select * into c from infrx.content_objects where content_id = p_id for update;
  if not found then
    perform infrx.lifecycle_refuse('not_found', 'no such content');
  end if;
  return c;
end $$;

-- The eligibility and reference rechecks: `not_eligible`, `reference_live`.
create or replace function infrx.content_recheck(c infrx.content_objects, p_now timestamptz)
returns void language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_why text;
begin
  if p_now < c.eligible_at then
    perform infrx.lifecycle_refuse('not_eligible', 'inside its persisted grace until '
                                   || c.eligible_at::text);
  end if;
  v_why := infrx.content_referenced(c, p_now);
  if v_why is not null then
    perform infrx.lifecycle_refuse('reference_live', v_why);
  end if;
end $$;

-- `claim`. Args `{content_id, generation, holder, claim_ttl_s}`.
create or replace function infrx.content_claim(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  c infrx.content_objects%rowtype;
begin
  if length(btrim(coalesce(p_args->>'holder', ''))) not between 1 and 200
     or coalesce((p_args->>'claim_ttl_s')::float8, 0) <= 0 then
    perform infrx.refuse('invalid_request', 'claim takes {content_id, generation, holder, '
                         || 'claim_ttl_s > 0}');
  end if;
  c := infrx.content_row((p_args->>'content_id')::uuid);
  if c.generation is distinct from (p_args->>'generation')::int or c.state = 'deleted' then
    perform infrx.lifecycle_refuse('claim_lost', 'another generation of this object');
  end if;
  if c.claim_generation = c.generation and v_now < c.claim_expires_at then
    perform infrx.lifecycle_refuse('claim_held', 'an unexpired claim holds this object');
  end if;
  if c.state = 'live' then
    perform infrx.content_recheck(c, v_now);
  end if;
  update infrx.content_objects
     set claim_generation = generation,
         claim_fence = case when claim_generation = generation then claim_fence + 1 else 1 end,
         claim_holder = btrim(p_args->>'holder'), claim_claimed_at = v_now,
         claim_expires_at = v_now + make_interval(secs => (p_args->>'claim_ttl_s')::float8)
   where content_id = c.content_id returning * into c;
  return infrx.claim_doc(c);
end $$;

-- `tombstone`. Args `{claim}` (a `DeletionClaim`). The recheck is inside, under the row lock
-- admission's FOR SHARE meets: a check before this transaction would be a race.
create or replace function infrx.content_tombstone(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  k jsonb := p_args->'claim';
  v_now timestamptz := infrx.now();
  c infrx.content_objects%rowtype;
begin
  c := infrx.content_row((k->>'content_id')::uuid);
  if c.generation is distinct from (k->>'generation')::int
     or c.claim_generation is distinct from c.generation
     or c.claim_fence is distinct from (k->>'fence')::int or v_now >= c.claim_expires_at then
    perform infrx.lifecycle_refuse('claim_lost', 'the claim is not the current, unexpired '
                                   || 'fence');
  end if;
  if c.state = 'live' then
    perform infrx.content_recheck(c, v_now);
    update infrx.content_objects set state = 'tombstoned', tombstoned_at = v_now
     where content_id = c.content_id returning * into c;
  end if;
  return jsonb_build_object('content_id', c.content_id, 'generation', c.generation,
    'fence', c.claim_fence, 'location', c.location, 'object_key', c.object_key,
    'tombstoned_at', c.tombstoned_at);
end $$;

-- `acknowledge_delete`. Args `{tombstone}`. Idempotent; an older generation's tombstone
-- changes nothing; a superseded fence is `claim_lost`. Database content is scrubbed HERE,
-- in this transaction (its delete is internal), before the row becomes `deleted`.
create or replace function infrx.content_acknowledge_delete(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  t jsonb := p_args->'tombstone';
  v_now timestamptz := infrx.now();
  c infrx.content_objects%rowtype;
begin
  c := infrx.content_row((t->>'content_id')::uuid);
  if c.generation is distinct from (t->>'generation')::int or c.state = 'deleted' then
    return infrx.content_doc(c);
  end if;
  if c.state <> 'tombstoned' or c.claim_generation is distinct from c.generation
     or c.claim_fence is distinct from (t->>'fence')::int then
    perform infrx.lifecycle_refuse('claim_lost', 'a newer claim finishes this delete');
  end if;
  if c.location = 'database' then
    perform infrx.scrub_content(c, v_now);
  end if;
  update infrx.content_objects set state = 'deleted', deleted_at = v_now
   where content_id = c.content_id returning * into c;
  return infrx.content_doc(c);
end $$;

-- ================================================ database content: the scrub ===
alter table infrx.job_results add column if not exists scrubbed_at timestamptz;
alter table infrx.jobs add column if not exists content_scrubbed_at timestamptz;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.job_results'::regclass
                 and conname = 'job_results_bytes_exact_or_scrubbed') then
    alter table infrx.job_results
      drop constraint job_results_bytes_exact,
      -- The measured size stays after the scrub; only an unscrubbed body must match it.
      add constraint job_results_bytes_exact_or_scrubbed check (
        (scrubbed_at is null and bytes = octet_length(body))
        or (scrubbed_at is not null and body = ''));
  end if;
end $$;

-- What an admitted request keeps once its content is scrubbed: identity, digests, sizes,
-- ceilings, instants, consent and the media refs' metadata - never the messages or the
-- parameters (the normalized request text).
create or replace function infrx.scrubbed_request(p_record jsonb) returns jsonb
language sql immutable set search_path = infrx, public, pg_temp as $$
  select (p_record - 'messages' - 'parameters') || '{"content_scrubbed": true}'::jsonb;
$$;

create or replace function infrx.scrub_content(c infrx.content_objects, p_now timestamptz)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if c.kind = 'result' then
    update infrx.job_results set body = '', scrubbed_at = p_now
     where request_id = c.job_id and org_id = c.org_id and scrubbed_at is null;
  elsif c.kind = 'payload' then
    update infrx.jobs set request_record = infrx.scrubbed_request(request_record),
                          content_scrubbed_at = p_now
     where request_id = c.job_id and org_id = c.org_id and content_scrubbed_at is null;
  end if;
end $$;

-- 0014 made a result row immutable; D10 allows exactly the scrub and nothing else: the body
-- emptied and `scrubbed_at` stamped once, only past the persisted expiry (or for a job that
-- keeps no result), every other column unchanged. DELETE and TRUNCATE stay refused.
create or replace function infrx.job_results_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' or old.scrubbed_at is not null or new.scrubbed_at is null
     or new.body <> '' or (new.request_id, new.org_id, new.digest, new.bytes, new.created_at)
        is distinct from (old.request_id, old.org_id, old.digest, old.bytes, old.created_at)
     or exists (select 1 from infrx.jobs j where j.request_id = old.request_id
                 and (j.settled_at is null or infrx.now() < j.result_expires_at)) then
    raise exception 'job_results is append-only: % is permitted only as the expiry scrub',
      tg_op using errcode = '23514';
  end if;
  return new;
end $$;
-- The trigger keeps 0014's name (so 0014 stays re-runnable) and runs the new guard.
create or replace trigger job_results_immutable before update or delete on infrx.job_results
  for each row execute function infrx.job_results_guard();

-- 0011's guard, plus the one permitted change: a TERMINAL job's request record replaced by
-- its scrubbed form, stamped once (`content_scrubbed_at` moves only with it).
create or replace function infrx.jobs_admission_record_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if new.content_scrubbed_at is distinct from old.content_scrubbed_at then
    if not (old.content_scrubbed_at is null and old.settled_at is not null
            and new.request_record is not distinct from
                infrx.scrubbed_request(old.request_record)) then
      raise exception 'job %: only a terminal job''s request record is scrubbed, once',
        old.request_id using errcode = '23514';
    end if;
  elsif new.request_record is distinct from old.request_record then
    raise exception 'job %: the admitted request and its prepared refs are immutable',
      old.request_id using errcode = '23514';
  end if;
  if new.idem_payload_hash is distinct from old.idem_payload_hash
     or (old.prepared_refs is not null
         and new.prepared_refs is distinct from old.prepared_refs)
     or (old.prepared_prompt_tokens is not null
         and new.prepared_prompt_tokens is distinct from old.prepared_prompt_tokens) then
    raise exception 'job %: the admitted request and its prepared refs are immutable',
      old.request_id using errcode = '23514';
  end if;
  return new;
end $$;

-- Database content is registered where it is written, by every writer (the previous
-- runtime's admissions too): no grace - its own job protects it (`content_referenced`). A
-- row the runtime registered first (the protocol's order) stands: the first registration
-- wins, and the database row itself is the content either way.
create or replace function infrx.register_database_content() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
begin
  if tg_table_name = 'job_results' then
    insert into infrx.content_objects (org_id, kind, location, object_key, digest, bytes,
      job_id, origin, registered_at, eligible_at)
    values (new.org_id, 'result', 'database', 'job_results/' || new.request_id, new.digest,
      new.bytes, new.request_id, 'written', v_now, v_now)
    on conflict (location, object_key) do nothing;
  elsif new.request_record is not null then
    insert into infrx.content_objects (org_id, kind, location, object_key, digest, bytes,
      job_id, origin, registered_at, eligible_at)
    values (new.org_id, 'payload', 'database', 'jobs/' || new.request_id,
      'sha256:' || encode(sha256(convert_to(new.request_record::text, 'UTF8')), 'hex'),
      octet_length(new.request_record::text), new.request_id, 'written', v_now, v_now)
    on conflict (location, object_key) do nothing;
  end if;
  return null;
end $$;
create or replace trigger job_results_content after insert on infrx.job_results
  for each row execute function infrx.register_database_content();
create or replace trigger jobs_request_content after insert on infrx.jobs
  for each row execute function infrx.register_database_content();

-- The rows written before 0020 (the operator's step, bounded and idempotent): at most
-- `limit` request records and result bodies with no content row are registered, with the
-- adapter's grace persisted as their eligibility. Args `{limit, grace_s}`; answers the count.
create or replace function infrx.register_existing_database_content(p_args jsonb)
returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_limit int := greatest(1, least(coalesce((p_args->>'limit')::int, 1000), 10000));
  v_grace float8 := (p_args->>'grace_s')::float8;
  v_count int := 0;
  r record;
begin
  if v_grace is null or v_grace < 0 then
    perform infrx.refuse('invalid_request', 'register_existing takes {limit, grace_s >= 0}');
  end if;
  for r in
    select 'result' as kind, x.org_id, x.request_id, x.digest, x.bytes::bigint
      from infrx.job_results x
     where x.scrubbed_at is null and not exists (select 1 from infrx.content_objects c
             where c.location = 'database' and c.object_key = 'job_results/' || x.request_id)
    union all
    select 'payload', j.org_id, j.request_id,
           'sha256:' || encode(sha256(convert_to(j.request_record::text, 'UTF8')), 'hex'),
           octet_length(j.request_record::text)
      from infrx.jobs j
     where j.request_record is not null and j.content_scrubbed_at is null
       and not exists (select 1 from infrx.content_objects c
             where c.location = 'database' and c.object_key = 'jobs/' || j.request_id)
    limit v_limit
  loop
    perform infrx.register_content(jsonb_build_object('org_id', r.org_id, 'kind', r.kind,
      'location', 'database', 'object_key', case r.kind when 'result' then 'job_results/'
                                                        else 'jobs/' end || r.request_id,
      'digest', r.digest, 'bytes', r.bytes, 'job_id', r.request_id, 'origin', 'written'),
      v_grace);
    v_count := v_count + 1;
  end loop;
  return to_jsonb(v_count);
end $$;

-- =================================================== the owner's result read ===
-- 0014's read, with the persisted expiry as its authority (RV-11 / F2C.b `read_outcome`):
-- the owner's stored result while `infrx.now() < jobs.result_expires_at`; before the job's
-- outcome commits `result_pending`; from the expiry on, with no persisted expiry, or once
-- scrubbed, `result_expired` - never empty text, and nothing regenerates it. Another
-- organization's reference is `not_found`, as before (checked first).
create or replace function infrx.read_result(p_org uuid, p_ref text) returns text
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_body text;
  v_scrubbed timestamptz;
  v_expires timestamptz;
  v_settled timestamptz;
begin
  select r.body, r.scrubbed_at, j.result_expires_at, j.settled_at
    into v_body, v_scrubbed, v_expires, v_settled
    from infrx.job_results r join infrx.jobs j on j.request_id = r.request_id
   where p_ref ~ '^infrx-result:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     and r.request_id = substr(p_ref, 14)::uuid and r.org_id = p_org;
  if not found then
    perform infrx.refuse('not_found', 'no result ' || coalesce(p_ref, ''));
  end if;
  if v_settled is null then
    perform infrx.refuse('result_pending', 'the job has no committed outcome yet');
  end if;
  if v_scrubbed is not null or v_expires is null or infrx.now() >= v_expires then
    perform infrx.refuse('result_expired', 'the result expired at '
                         || coalesce(v_expires::text, 'an unrecorded instant'));
  end if;
  return v_body;
end $$;

-- ================================================================ privileges ===
do $$
declare
  f text;
begin
  foreach f in array array[
      'infrx.claim_doc(infrx.content_objects)',
      'infrx.content_referenced(infrx.content_objects, timestamp with time zone)',
      'infrx.content_row(uuid)',
      'infrx.content_recheck(infrx.content_objects, timestamp with time zone)',
      'infrx.scrubbed_request(jsonb)',
      'infrx.scrub_content(infrx.content_objects, timestamp with time zone)',
      'infrx.job_results_guard()', 'infrx.register_database_content()']
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role',
                   f);
  end loop;
  foreach f in array array[
      'infrx.content_candidates(jsonb)', 'infrx.content_claim(jsonb)',
      'infrx.content_tombstone(jsonb)', 'infrx.content_acknowledge_delete(jsonb)',
      'infrx.register_existing_database_content(jsonb)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
end $$;
comment on function infrx.read_result(uuid, text) is
  'D2 (0014); D10 (0020): the owner''s result while now < the persisted result_expires_at; '
  'result_expired from it on, with none, or once scrubbed. service_role only.';
