-- D10.a: durable upload tickets, content reference rows and the execution-ready marker
-- (consumer-v1/01 §D10.a; RV-02, RV-05; F2C.a `contracts/v2/lifecycle.py`: UploadRepository,
-- ReadinessStore, ContentLifecycle.register; 08 §10 R7, R10, R55, R69, R99).
--
--   media_uploads (0010, extended)   the ticket: constraints, owner, window, the bytes the
--                                    server measured at the destination (`received_*`) and
--                                    the immutable finalized source it names
--                                    (`source_content_id`, `source_generation`). Reloaded
--                                    whole by any process; process memory is never authority.
--   content_objects                  ONE durable row per stored object (object store key, or
--                                    a database content row), with a generation that moves
--                                    only when a deleted key is registered again. What
--                                    protects each object is derived from persisted rows
--                                    (0020 adds the deletion claims that read it).
--   job_media (0003, extended)       the manifest: each source a job executes on, bound to a
--                                    content row generation (`content_id`, `generation`).
--   job_readiness                    the execution-ready marker: written in the admission
--                                    transaction, never changed. A job with ZERO media has
--                                    the marker and no manifest row; a job without the
--                                    marker is "never completed", which is not the same.
--
--   admit_ready                      ONE transaction: everything `infrx.admit` does, then the
--                                    runtime's expectation against the pins (R69), the
--                                    PINNED serving revision's capability, every media ref
--                                    resolved to a live content row of the request's own
--                                    organization (an upload also to its finalized,
--                                    unexpired ticket), the manifest and the marker. A
--                                    refusal admits nothing: no job, hold or outbox.
--   claim_preparation_ready          `ReadinessStore.claim_preparation`: no preparation
--                                    lease for a job without the marker, ever. 0012's
--                                    `claim_preparation` stays the previous runtime's door.
--   readiness_cutover_check          the operator's cutover gate (I8 scripts it): admission
--                                    paused and no preparing job without a marker.
--
-- TIME: every instant is `infrx.now()` (R7), never a caller's. The upload window and the
-- content grace are the ADAPTER's configured lengths (`window_s`, `grace_s`), persisted once
-- as `expires_at` / `eligible_at` - a later configuration never moves them. A window is live
-- while `now < instant`: at equality it has passed.
--
-- LOCK ORDER (continues 0011's 1-5 and 0018's). Admission: 1-5, the new job row, then per
-- source the upload ticket (FOR SHARE) before its content row (FOR SHARE). Upload
-- completion: the ticket (FOR UPDATE) before the content row (FOR UPDATE). `register`: the
-- content row only. 0020's `tombstone`: the content row only (reads, never locks, jobs and
-- tickets). So a ticket is always taken before a content row and no path takes 1-5 after
-- either: no cycle. Attach and delete meet on the content row: whichever commits first
-- wins, and the other sees it (admission: `content_retiring`; tombstone: `reference_live`).
--
-- ROLLOUT (expand; D10 evidence). Additive, and no earlier function body changes except
-- 0010's `media_uploads_guard` (a stricter superset). Only `admit_ready` writes a marker;
-- `admit` / `admit_credit` (the previous runtime) never do, and the previous runtime's
-- worker keeps using 0012's `claim_preparation`, so it runs unchanged against this schema,
-- and a job `admit_ready` admitted carries the 0003 `job_media` rows that worker reads.
-- CUTOVER (old -> new runtime): pause admission (the `credit_admission` /
-- `legacy_usd_admission` flags), wait until `readiness_cutover_check()` answers
-- `ready: true` (the previous worker has prepared, or 0016's reaper has ended, every
-- preparing job without a marker - released free at its preparation deadline, never
-- billed), start the new runtime, resume admission. There is no backfill: a marker is
-- written by the admission transaction or not at all.
-- ROLLBACK to the previous runtime: stop the new one, start the old one; nothing to undo.
--
-- ROLLBACK (0019 itself). Re-run 0010's `media_uploads_guard`; drop the functions this file
-- adds, `job_readiness`, then `content_objects` (after `job_media` and `media_uploads` drop
-- the columns referencing it). 0010's grants to service_role on `media_uploads` come back
-- with `grant insert, update, delete`. Accepted jobs, holds and money are untouched.
--
-- Additive and re-runnable.

-- ============================================================= refusals ===
-- F2C.a `LifecycleRefusal` -> the public code (`REFUSAL_ERRORS`), raised as P0001
-- `<code>: <detail>` with the reason in the hint (`refusal=<reason>`), so the adapter
-- rebuilds the typed error with `.refusal` and nothing public changes.
create or replace function infrx.lifecycle_code(p_reason text) returns text
language sql immutable set search_path = infrx, public, pg_temp as $$
  select case p_reason
    when 'not_found' then 'not_found'
    when 'invalid_constraints' then 'invalid_request'
    when 'upload_expired' then 'upload_expired'
    when 'upload_not_open' then 'state_conflict'
    when 'upload_not_finalized' then 'invalid_request'
    when 'nothing_received' then 'invalid_request'
    when 'bytes_changed' then 'state_conflict'
    when 'too_large' then 'request_too_large'
    when 'size_mismatch' then 'invalid_request'
    when 'digest_mismatch' then 'unsupported_media'
    when 'mime_not_accepted' then 'unsupported_media'
    when 'media_refused' then 'unsupported_media'
    when 'invalid_manifest' then 'invalid_request'
    when 'expectation_mismatch' then 'invalid_request'
    when 'not_ready' then 'not_claimable'
    when 'content_retiring' then 'dependency_unavailable'
    when 'not_eligible' then 'not_claimable'
    when 'reference_live' then 'not_claimable'
    when 'claim_held' then 'not_claimable'
    when 'claim_lost' then 'stale_lease'
  end;
$$;

create or replace function infrx.lifecycle_refuse(p_reason text, p_detail text)
returns void language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if infrx.lifecycle_code(p_reason) is null then
    raise exception 'unknown lifecycle refusal %', p_reason using errcode = 'XX000';
  end if;
  raise exception using errcode = 'P0001',
    message = infrx.lifecycle_code(p_reason) || ': ' || p_detail,
    hint = 'refusal=' || p_reason;
end $$;

-- A refusal that follows a write which must commit (R39): answered as data.
create or replace function infrx.lifecycle_refusal(p_reason text, p_detail text)
returns jsonb language sql immutable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('reason', p_reason, 'code', infrx.lifecycle_code(p_reason),
                            'detail', p_detail);
$$;

-- ======================================================== content objects ===
create table if not exists infrx.content_objects (
  content_id uuid primary key default gen_random_uuid(),
  generation int not null default 1 check (generation >= 1),
  org_id uuid not null references public.organizations(id) on delete restrict,
  kind text not null
    check (kind in ('upload_destination', 'source', 'prepared', 'payload', 'result')),
  location text not null check (location in ('object_store', 'database')),
  object_key text not null check (length(object_key) between 1 and 1024),
  digest text check (digest ~ '^sha256:[0-9a-f]{64}$'),
  bytes bigint check (bytes >= 0),
  job_id uuid,
  upload_handle text check (upload_handle ~ '^upl_[A-Za-z0-9_-]{22,64}$'),
  origin text not null check (origin in ('written', 'discovered')),
  state text not null default 'live' check (state in ('live', 'tombstoned', 'deleted')),
  registered_at timestamptz not null,
  eligible_at timestamptz not null,
  tombstoned_at timestamptz,
  deleted_at timestamptz,
  constraint content_objects_location_key unique (location, object_key),
  -- payload, prepared and result content serve ONE job; sources and destinations none. A
  -- payload or prepared object a collector DISCOVERED (written before any row existed) may
  -- name no job: its owner is its key's organization, and only a running job that names the
  -- key keeps it (`content_referenced`, 0020) - proposed to F2C for ContentIdentity.
  constraint content_objects_owner_by_kind check (
    (case when kind = 'result' then job_id is not null
          when kind in ('payload', 'prepared')
            then job_id is not null or origin = 'discovered'
          else job_id is null end)
    and (kind = 'upload_destination') = (upload_handle is not null)),
  constraint content_objects_written_is_measured
    check (origin <> 'written' or (digest is not null and bytes is not null)),
  constraint content_objects_eligible_after_registration check (eligible_at >= registered_at),
  constraint content_objects_state_instants check (
    (state = 'live') = (tombstoned_at is null)
    and (state = 'deleted') = (deleted_at is not null)),
  -- Never outside the tenant's own prefix (the adapter adds the environment's bucket
  -- prefix): a row cannot name another organization's object, or anything else; a `.`/`..`
  -- segment would reach outside the prefix on a store that normalizes keys (M6 finding).
  constraint content_objects_key_in_tenant_prefix check (case location
    when 'object_store' then object_key like any (array[
      'media/' || org_id::text || '/%', 'uploads/' || org_id::text || '/%',
      'payloads/' || org_id::text || '/%']) and object_key !~ '(^|/)\.\.?(/|$)'
    -- database content is `<table>/<row id>`: a result body or an admitted request record
    else job_id is not null and (kind, object_key) in (('result', 'job_results/' || job_id::text),
                                                       ('payload', 'jobs/' || job_id::text)) end),
  -- R55: a destination names its OWN organization's ticket.
  constraint content_objects_destination_fk foreign key (org_id, upload_handle)
    references infrx.media_uploads (org_id, handle) on delete restrict
);
create index if not exists content_objects_candidates_idx
  on infrx.content_objects (eligible_at, content_id) where state <> 'deleted';
create index if not exists content_objects_job_idx on infrx.content_objects (job_id)
  where job_id is not null;

-- Identity is fixed per generation; the generation moves only deleted -> live.
create or replace function infrx.content_objects_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'content %: a content row is its object''s history; it is not deleted',
      old.content_id using errcode = '23514';
  end if;
  if new.content_id is distinct from old.content_id or new.org_id is distinct from old.org_id
     or new.location is distinct from old.location
     or new.object_key is distinct from old.object_key then
    raise exception 'content %: the key and its tenant are immutable', old.content_id
      using errcode = '23514';
  end if;
  if new.generation <> old.generation then
    if not (old.state = 'deleted' and new.state = 'live'
            and new.generation = old.generation + 1) then
      raise exception 'content %: a generation starts only after a delete', old.content_id
        using errcode = '23514';
    end if;
    return new;
  end if;
  if new.kind is distinct from old.kind or new.digest is distinct from old.digest
     or new.bytes is distinct from old.bytes or new.job_id is distinct from old.job_id
     or new.upload_handle is distinct from old.upload_handle
     or new.origin is distinct from old.origin
     or new.registered_at is distinct from old.registered_at
     or new.eligible_at is distinct from old.eligible_at then
    raise exception 'content %: a generation''s identity and eligibility are immutable',
      old.content_id using errcode = '23514';
  end if;
  if new.state is distinct from old.state
     and not ((old.state = 'live' and new.state = 'tombstoned')
              or (old.state = 'tombstoned' and new.state = 'deleted')) then
    raise exception 'content %: % -> % is not an allowed transition', old.content_id,
      old.state, new.state using errcode = '23514';
  end if;
  if old.state <> 'live' and (new.tombstoned_at is distinct from old.tombstoned_at) then
    raise exception 'content %: the tombstone instant is written once', old.content_id
      using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger content_objects_guard before update or delete
  on infrx.content_objects for each row execute function infrx.content_objects_guard();

create or replace function infrx.content_doc(c infrx.content_objects) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'content_id', c.content_id, 'generation', c.generation,
    'identity', jsonb_build_object('org_id', c.org_id, 'kind', c.kind,
      'location', c.location, 'object_key', c.object_key, 'digest', c.digest,
      'bytes', c.bytes, 'job_id', c.job_id, 'upload_handle', c.upload_handle,
      'origin', c.origin),
    'state', c.state, 'registered_at', c.registered_at, 'eligible_at', c.eligible_at,
    'tombstoned_at', c.tombstoned_at, 'deleted_at', c.deleted_at);
$$;

-- `ContentLifecycle.register` (the write every other path shares): before the runtime writes
-- an object (`written`), or for an object a collector found with no row (`discovered`).
-- Idempotent on (location, key): the first registration wins and its eligibility is never
-- reset; a tombstoned key is `content_retiring` until its delete is acknowledged; a deleted
-- key becomes generation + 1 (0020 adds: not before the claim that deleted it has lapsed, so
-- a delete still inside its lease cannot remove the new bytes); other bytes at a live key are
-- `bytes_changed`. Takes the content row only. `p_grace_s` is the adapter's configured
-- grace, persisted as `eligible_at`.
create or replace function infrx.register_content(p_identity jsonb, p_grace_s float8)
returns infrx.content_objects
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  c infrx.content_objects%rowtype;
begin
  if jsonb_typeof(p_identity) is distinct from 'object' or p_grace_s is null
     or p_grace_s < 0 then
    perform infrx.refuse('invalid_request', 'register takes {identity, grace_s >= 0}');
  end if;
  -- Ambiguous ownership is retained and reported, never registered: a key outside the
  -- organization's own prefix, or an organization that does not exist.
  if not exists (select 1 from public.organizations o where o.id::text = p_identity->>'org_id')
     or (p_identity->>'location' = 'object_store' and not coalesce(
           p_identity->>'object_key' like any (array[
             'media/' || (p_identity->>'org_id') || '/%',
             'uploads/' || (p_identity->>'org_id') || '/%',
             'payloads/' || (p_identity->>'org_id') || '/%']), false)
           or p_identity->>'object_key' ~ '(^|/)\.\.?(/|$)') then
    perform infrx.lifecycle_refuse('not_found', 'the key is not under this organization''s '
                                   || 'prefix');
  end if;
  select * into c from infrx.content_objects
   where location = p_identity->>'location' and object_key = p_identity->>'object_key'
   for update;
  if not found then
    begin
      insert into infrx.content_objects (org_id, kind, location, object_key, digest, bytes,
        job_id, upload_handle, origin, registered_at, eligible_at)
      values ((p_identity->>'org_id')::uuid, p_identity->>'kind', p_identity->>'location',
        p_identity->>'object_key', p_identity->>'digest', (p_identity->>'bytes')::bigint,
        (p_identity->>'job_id')::uuid, p_identity->>'upload_handle', p_identity->>'origin',
        v_now, v_now + make_interval(secs => p_grace_s))
      returning * into c;
      return c;
    exception when unique_violation then
      -- A concurrent first registration of the same key committed meanwhile: its row.
      select * into c from infrx.content_objects
       where location = p_identity->>'location' and object_key = p_identity->>'object_key'
       for update;
    end;
  end if;
  -- R10: another organization's key is the same answer as none.
  if c.org_id is distinct from (p_identity->>'org_id')::uuid then
    perform infrx.lifecycle_refuse('not_found', 'no content at that key for this org');
  end if;
  if c.state = 'tombstoned' then
    perform infrx.lifecycle_refuse('content_retiring',
      'the object at this key is being deleted; register it again after the delete');
  end if;
  if c.state = 'deleted' then
    update infrx.content_objects
       set generation = generation + 1, kind = p_identity->>'kind',
           digest = p_identity->>'digest', bytes = (p_identity->>'bytes')::bigint,
           job_id = (p_identity->>'job_id')::uuid,
           upload_handle = p_identity->>'upload_handle', origin = p_identity->>'origin',
           state = 'live', registered_at = v_now,
           eligible_at = v_now + make_interval(secs => p_grace_s),
           tombstoned_at = null, deleted_at = null
     where content_id = c.content_id
    returning * into c;
    return c;
  end if;
  -- The first registration's identity stands (a prepared object shared by two jobs of the
  -- organization keeps the first job's); only other bytes are refused.
  if (c.digest is not null and p_identity->>'digest' is not null
      and c.digest <> p_identity->>'digest')
     or (c.bytes is not null and p_identity->>'bytes' is not null
         and c.bytes <> (p_identity->>'bytes')::bigint) then
    perform infrx.lifecycle_refuse('bytes_changed', 'this key names other bytes');
  end if;
  return c;
end $$;

create or replace function infrx.content_register(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  return infrx.content_doc(infrx.register_content(p_args->'identity',
                                                  (p_args->>'grace_s')::float8));
end $$;

-- ========================================================= upload tickets ===
alter table infrx.media_uploads
  add column if not exists received_bytes bigint,
  add column if not exists received_digest text,
  add column if not exists received_at timestamptz,
  add column if not exists source_content_id uuid references infrx.content_objects
    on delete restrict,
  add column if not exists source_generation int;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.media_uploads'::regclass
                 and conname = 'media_uploads_receipt_is_one_fact') then
    alter table infrx.media_uploads
      add constraint media_uploads_receipt_is_one_fact check (
        num_nonnulls(received_bytes, received_digest, received_at) in (0, 3)
        and (received_digest is null or received_digest ~ '^sha256:[0-9a-f]{64}$')
        and (received_bytes is null or received_bytes between 0 and max_bytes)
        and (received_at is null
             or (received_at >= created_at and received_at < expires_at))),
      add constraint media_uploads_source_is_one_fact check (
        (source_content_id is null) = (source_generation is null)
        and (source_generation is null or source_generation >= 1)),
      -- A finalized ticket names exactly the bytes received, inside its window.
      add constraint media_uploads_finalized_is_the_receipt check (
        state <> 'finalized' or (source_content_id is not null
          and received_digest = digest and received_bytes = bytes
          and finalized_at >= received_at and finalized_at < expires_at)),
      add constraint media_uploads_aborted_has_reason
        check (state <> 'aborted' or aborted_reason is not null);
  end if;
end $$;
create index if not exists media_uploads_source_idx on infrx.media_uploads (source_content_id)
  where source_content_id is not null;

-- 0010's guard, plus: the receipt is written once (one handle names one set of bytes).
create or replace function infrx.media_uploads_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    if old.state = 'finalized' and infrx.now() < old.expires_at then
      raise exception 'upload %: a finalized upload is kept until it expires', old.handle
        using errcode = '23514';
    end if;
    return old;
  end if;
  if new.handle is distinct from old.handle or new.org_id is distinct from old.org_id
     or new.max_bytes is distinct from old.max_bytes
     or new.declared_bytes is distinct from old.declared_bytes
     or new.declared_digest is distinct from old.declared_digest
     or new.accepted_mime is distinct from old.accepted_mime
     or new.created_at is distinct from old.created_at
     or new.expires_at is distinct from old.expires_at then
    raise exception 'upload %: identity and constraints are immutable', old.handle
      using errcode = '23514';
  end if;
  if old.received_at is not null
     and (new.received_at is distinct from old.received_at
          or new.received_bytes is distinct from old.received_bytes
          or new.received_digest is distinct from old.received_digest) then
    raise exception 'upload %: the received bytes are recorded once', old.handle
      using errcode = '23514';
  end if;
  if new.state is distinct from old.state and not (old.state = 'created'
       and new.state in ('finalized', 'aborted', 'expired')) then
    raise exception 'upload %: % -> % is not an allowed transition', old.handle,
      old.state, new.state using errcode = '23514';
  end if;
  if old.state <> 'created' and row(new.*) is distinct from row(old.*) then
    raise exception 'upload % is %: it is immutable', old.handle, old.state
      using errcode = '23514';
  end if;
  return new;
end $$;

-- `UploadTicket` as F2C.a spells it. No object key: M builds that from (org, profile, digest).
create or replace function infrx.upload_doc(u infrx.media_uploads) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'upload_handle', u.handle, 'org_id', u.org_id,
    'destination_ref', 'infrx-upload:' || u.handle,
    'constraints', jsonb_build_object('max_bytes', u.max_bytes, 'bytes', u.declared_bytes,
      'accepted_mime', to_jsonb(u.accepted_mime), 'digest', u.declared_digest),
    'state', u.state, 'created_at', u.created_at, 'expires_at', u.expires_at,
    'received', case when u.received_at is not null then jsonb_build_object(
      'bytes', u.received_bytes, 'digest', u.received_digest,
      'received_at', u.received_at) end,
    'finalized', case when u.state = 'finalized' then jsonb_build_object(
      'content_id', u.source_content_id, 'generation', u.source_generation,
      'digest', u.digest, 'bytes', u.bytes, 'mime', u.mime,
      'profile_version', u.profile_version, 'duration_s', u.duration_s,
      'finalized_at', u.finalized_at) end,
    'refusal', u.aborted_reason);
$$;

-- The ticket of (org, handle), locked; another organization's is `not_found` (R10).
create or replace function infrx.upload_row(p_org uuid, p_handle text, p_lock text)
returns infrx.media_uploads
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  u infrx.media_uploads%rowtype;
begin
  if p_lock = 'update' then
    select * into u from infrx.media_uploads where org_id = p_org and handle = p_handle
      for update;
  else
    select * into u from infrx.media_uploads where org_id = p_org and handle = p_handle
      for share;
  end if;
  if not found then
    perform infrx.lifecycle_refuse('not_found', 'no upload ' || coalesce(p_handle, '')
                                   || ' owned by this organization');
  end if;
  return u;
end $$;

-- `UploadRepository.create`. Args `{org_id, upload_handle, constraints, window_s}`: the
-- adapter mints the opaque handle and names its configured window; the caller of the port
-- names neither.
create or replace function infrx.upload_create(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  c jsonb := p_args->'constraints';
  v_now timestamptz := infrx.now();
  u infrx.media_uploads%rowtype;
begin
  if jsonb_typeof(c) is distinct from 'object'
     or jsonb_typeof(c->'max_bytes') is distinct from 'number'
     or jsonb_typeof(c->'accepted_mime') is distinct from 'array'
     or (c->'bytes' is not null and jsonb_typeof(c->'bytes') not in ('number', 'null'))
     or (p_args->>'window_s')::float8 is null or (p_args->>'window_s')::float8 <= 0 then
    perform infrx.lifecycle_refuse('invalid_constraints',
      'create takes {org_id, upload_handle, constraints, window_s > 0}');
  end if;
  begin
    insert into infrx.media_uploads (handle, org_id, state, max_bytes, declared_bytes,
      declared_digest, accepted_mime, created_at, expires_at)
    values (p_args->>'upload_handle', (p_args->>'org_id')::uuid, 'created',
      (c->>'max_bytes')::bigint, (c->>'bytes')::bigint, c->>'digest',
      array(select jsonb_array_elements_text(c->'accepted_mime')), v_now,
      v_now + make_interval(secs => (p_args->>'window_s')::float8))
    returning * into u;
  exception when check_violation or invalid_text_representation
                 or numeric_value_out_of_range then
    perform infrx.lifecycle_refuse('invalid_constraints', 'constraint values');
  end;
  return infrx.upload_doc(u);
end $$;

-- `UploadRepository.acknowledge_put`. Args `{org_id, upload_handle, bytes, digest}`: what the
-- SERVER measured at the destination.
create or replace function infrx.upload_acknowledge_put(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_bytes bigint := (p_args->>'bytes')::bigint;
  v_digest text := p_args->>'digest';
  u infrx.media_uploads%rowtype;
begin
  if v_bytes is null or v_bytes < 0 or coalesce(v_digest, '') !~ '^sha256:[0-9a-f]{64}$' then
    perform infrx.refuse('invalid_request', 'acknowledge_put takes {bytes >= 0, digest}');
  end if;
  u := infrx.upload_row((p_args->>'org_id')::uuid, p_args->>'upload_handle', 'update');
  if u.state <> 'created' then
    perform infrx.lifecycle_refuse('upload_not_open', 'upload ' || u.handle || ' is ' || u.state);
  end if;
  if v_now >= u.expires_at then
    perform infrx.lifecycle_refuse('upload_expired', 'the upload window closed at '
                                   || u.expires_at::text);
  end if;
  if v_bytes > u.max_bytes then
    perform infrx.lifecycle_refuse('too_large', v_bytes || ' bytes over the upload''s '
                                   || u.max_bytes);
  end if;
  if u.received_at is not null then
    if (u.received_bytes, u.received_digest) is distinct from (v_bytes, v_digest) then
      perform infrx.lifecycle_refuse('bytes_changed', 'upload ' || u.handle
                                     || ' already received other bytes');
    end if;
    return infrx.upload_doc(u);
  end if;
  update infrx.media_uploads set received_bytes = v_bytes, received_digest = v_digest,
                                 received_at = v_now
   where handle = u.handle returning * into u;
  return infrx.upload_doc(u);
end $$;

-- `UploadRepository.complete`. Args `{org_id, upload_handle, source, grace_s}` (`source` a
-- `MediaRef`, M's verified description at the content-addressed source key). Answers the
-- ticket, or `{"refusal", "ticket"}` when a failed constraint ABORTED it - that must
-- commit (a failed check is final), so it is data, not an exception (R39).
create or replace function infrx.upload_complete(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  s jsonb := p_args->'source';
  v_now timestamptz := infrx.now();
  v_reason text;
  u infrx.media_uploads%rowtype;
  c infrx.content_objects%rowtype;
begin
  if jsonb_typeof(s) is distinct from 'object' then
    perform infrx.refuse('invalid_request', 'complete takes {org_id, upload_handle, source}');
  end if;
  u := infrx.upload_row((p_args->>'org_id')::uuid, p_args->>'upload_handle', 'update');
  -- A ref of another tenant, or relabelled as another handle or kind, is not this upload's.
  if s->>'org_id' is distinct from u.org_id::text or s->>'handle' is distinct from u.handle
     or s->>'kind' is distinct from 'upload' then
    perform infrx.lifecycle_refuse('not_found', 'the source is not this upload''s');
  end if;
  if u.state = 'finalized' then
    if (u.digest, u.bytes, u.mime, u.storage_ref) is distinct from
       (s->>'digest', (s->>'bytes')::bigint, s->>'mime', s->>'storage_ref') then
      perform infrx.lifecycle_refuse('bytes_changed', 'upload ' || u.handle
                                     || ' was finalized with other bytes');
    end if;
    return infrx.upload_doc(u);
  end if;
  if u.state <> 'created' then
    perform infrx.lifecycle_refuse('upload_not_open', 'upload ' || u.handle || ' is ' || u.state);
  end if;
  if v_now >= u.expires_at then
    perform infrx.lifecycle_refuse('upload_expired', 'the upload window closed at '
                                   || u.expires_at::text);
  end if;
  if u.received_at is null then
    perform infrx.lifecycle_refuse('nothing_received',
                                   'no object was uploaded to the issued destination');
  end if;
  if (u.received_digest, u.received_bytes) is distinct from
     (s->>'digest', (s->>'bytes')::bigint) then
    perform infrx.lifecycle_refuse('bytes_changed', 'the source is not the bytes received');
  end if;
  -- 0010 / F2C.a FinalizedSource: an upload whose duration was not measured is not final.
  if (s->>'duration_s')::float8 is null then
    perform infrx.refuse('invalid_request', 'a finalized upload carries its measured duration');
  end if;
  v_reason := case
    when u.declared_bytes is not null and u.declared_bytes <> (s->>'bytes')::bigint
      then 'size_mismatch'
    when u.declared_digest is not null and u.declared_digest <> s->>'digest'
      then 'digest_mismatch'
    when not coalesce(s->>'mime' = any (u.accepted_mime), false) then 'mime_not_accepted' end;
  if v_reason is not null then
    update infrx.media_uploads set state = 'aborted', aborted_reason = v_reason
     where handle = u.handle returning * into u;
    return jsonb_build_object('refusal', infrx.lifecycle_refusal(v_reason,
                              'upload ' || u.handle || ': ' || v_reason),
                              'ticket', infrx.upload_doc(u));
  end if;
  c := infrx.register_content(jsonb_build_object('org_id', u.org_id, 'kind', 'source',
         'location', 'object_store', 'object_key', s->>'storage_ref',
         'digest', s->>'digest', 'bytes', (s->>'bytes')::bigint, 'origin', 'written'),
       (p_args->>'grace_s')::float8);
  update infrx.media_uploads
     set state = 'finalized', finalized_at = v_now, digest = s->>'digest',
         bytes = (s->>'bytes')::bigint, mime = s->>'mime',
         duration_s = (s->>'duration_s')::float8, storage_ref = s->>'storage_ref',
         profile_version = coalesce(s->>'profile_version', 'v1'),
         source_content_id = c.content_id, source_generation = c.generation
   where handle = u.handle returning * into u;
  return infrx.upload_doc(u);
end $$;

-- `UploadRepository.abort`: M's own final refusal. Idempotent; a finalized (or expired)
-- ticket is `upload_not_open`.
create or replace function infrx.upload_abort(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  u infrx.media_uploads%rowtype;
begin
  -- F2C.a UPLOAD_ABORT_REASONS: a ticket only ever records a public reason about the
  -- caller's own bytes.
  if coalesce(p_args->>'refusal', '') not in ('too_large', 'size_mismatch', 'digest_mismatch',
                                             'mime_not_accepted', 'media_refused') then
    perform infrx.refuse('invalid_request', 'abort takes one of the upload abort reasons');
  end if;
  u := infrx.upload_row((p_args->>'org_id')::uuid, p_args->>'upload_handle', 'update');
  if u.state = 'aborted' then
    return infrx.upload_doc(u);
  end if;
  -- A ticket past its window is closed to every step, an abort included (F2C.a / M5).
  if u.state <> 'created' or infrx.now() >= u.expires_at then
    perform infrx.lifecycle_refuse('upload_not_open', 'upload ' || u.handle || ' is '
                                   || u.state || ' or past its window');
  end if;
  update infrx.media_uploads set state = 'aborted', aborted_reason = p_args->>'refusal'
   where handle = u.handle returning * into u;
  return infrx.upload_doc(u);
end $$;

-- `UploadRepository.resolve` (R99): `not_found`, then `upload_expired` from `expires_at` on
-- (any state), then `upload_not_finalized`, then `not_found` when the finalized source row
-- is no longer live at the finalized generation. Read-only.
create or replace function infrx.upload_resolve(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  u infrx.media_uploads%rowtype;
begin
  select * into u from infrx.media_uploads
   where org_id = (p_args->>'org_id')::uuid and handle = p_args->>'upload_handle';
  if not found then
    perform infrx.lifecycle_refuse('not_found', 'no upload ' || coalesce(
      p_args->>'upload_handle', '') || ' owned by this organization');
  end if;
  if infrx.now() >= u.expires_at then
    perform infrx.lifecycle_refuse('upload_expired', 'the upload window has expired');
  end if;
  if u.state <> 'finalized' then
    perform infrx.lifecycle_refuse('upload_not_finalized', 'upload ' || u.handle || ' is '
                                   || u.state || ', not finalized');
  end if;
  if not exists (select 1 from infrx.content_objects c
                  where c.content_id = u.source_content_id
                    and c.generation = u.source_generation and c.state = 'live') then
    perform infrx.lifecycle_refuse('not_found', 'the object for upload ' || u.handle
                                   || ' is gone');
  end if;
  return infrx.upload_doc(u);
end $$;

-- `UploadRepository.expire`: at most `limit` open tickets past their window, SKIP LOCKED.
create or replace function infrx.upload_expire(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_limit int := greatest(1, least(coalesce((p_args->>'limit')::int, 1000), 10000));
  v_count int;
begin
  with due as (select handle from infrx.media_uploads
                where state = 'created' and expires_at <= infrx.now()
                order by expires_at, handle limit v_limit for update skip locked),
       done as (update infrx.media_uploads u set state = 'expired' from due
                 where u.handle = due.handle returning 1)
  select count(*) into v_count from done;
  return to_jsonb(v_count);
end $$;

-- ================================================ the manifest and the marker ===
alter table infrx.job_media
  add column if not exists content_id uuid references infrx.content_objects
    on delete restrict,
  add column if not exists generation int;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.job_media'::regclass
                 and conname = 'job_media_content_is_one_fact') then
    alter table infrx.job_media add constraint job_media_content_is_one_fact
      check ((content_id is null) = (generation is null)
             and (generation is null or generation >= 1));
  end if;
end $$;
create index if not exists job_media_content_idx on infrx.job_media (content_id, generation)
  where content_id is not null;

create table if not exists infrx.job_readiness (
  job_id uuid primary key,
  org_id uuid not null,
  ready_at timestamptz not null,
  source_count int not null check (source_count >= 0),
  -- How long the job's content stays referenced after it ends: the adapter's configured
  -- retention (P-25 pending), captured at admission, so a reference's `retain_until` is
  -- `settled_at + retention_s` - two immutable facts, never today's configuration.
  retention_s double precision not null check (retention_s >= 0),
  constraint job_readiness_job_fk foreign key (job_id, org_id)
    references infrx.jobs (request_id, org_id) on delete cascade
);
do $$
begin
  if not exists (select 1 from pg_trigger where tgname = 'job_readiness_immutable') then
    create trigger job_readiness_immutable before update on infrx.job_readiness
      for each row execute function infrx.forbid_update_delete();
    create trigger infrx_job_readiness_no_truncate before truncate on infrx.job_readiness
      for each statement execute function infrx.forbid_truncate();
  end if;
end $$;

-- `ExecutionReadiness`, or NULL when no marker was ever completed (never the empty manifest).
create or replace function infrx.readiness_doc(p_job uuid) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('job_id', r.job_id, 'org_id', r.org_id, 'ready_at', r.ready_at,
    'sources', coalesce((select jsonb_agg(jsonb_build_object(
        'content_id', m.content_id, 'generation', m.generation,
        'ref', jsonb_build_object('org_id', s.org_id, 'handle', s.handle, 'kind', s.kind,
          'digest', s.digest, 'bytes', s.bytes, 'mime', s.mime,
          'storage_ref', s.storage_ref, 'profile_version', s.profile_version,
          'duration_s', s.duration_s)) order by m.position)
      from infrx.job_media m
      join infrx.staged_media s on s.org_id = m.org_id and s.handle = m.handle
     where m.job_id = r.job_id and m.role = 'source'), '[]'))
  from infrx.job_readiness r where r.job_id = p_job;
$$;

-- `ContentLifecycle.references`: every manifest reference of an object, with its
-- `retain_until` = the job's `settled_at` + the retention its admission captured (NULL while
-- the job runs; a job with no marker captured none). Another tenant cannot name a
-- content id it never saw, but an unknown one is `not_found` all the same.
create or replace function infrx.content_references(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if not exists (select 1 from infrx.content_objects
                  where content_id = (p_args->>'content_id')::uuid) then
    perform infrx.lifecycle_refuse('not_found', 'no such content');
  end if;
  return coalesce((select jsonb_agg(jsonb_build_object(
      'content_id', m.content_id, 'generation', m.generation, 'job_id', m.job_id,
      'org_id', m.org_id, 'referenced_at', m.attached_at,
      'retain_until', j.settled_at + make_interval(secs => coalesce(r.retention_s, 0)))
      order by m.attached_at, m.job_id)
    from infrx.job_media m
    join infrx.jobs j on j.request_id = m.job_id and j.org_id = m.org_id
    left join infrx.job_readiness r on r.job_id = m.job_id
   where m.content_id = (p_args->>'content_id')::uuid and m.role = 'source'), '[]');
end $$;

-- One manifest source: the upload ticket (FOR SHARE) before the content row (FOR SHARE),
-- then the staged description and the binding. Refusals raise: nothing is admitted.
create or replace function infrx.bind_source(j infrx.jobs, m jsonb, p_position int)
returns void language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  u infrx.media_uploads%rowtype;
  c infrx.content_objects%rowtype;
  v_recorded text;
begin
  if m->>'kind' = 'upload' then
    u := infrx.upload_row(j.org_id, m->>'handle', 'share');
    if v_now >= u.expires_at then
      perform infrx.lifecycle_refuse('upload_expired', 'the upload window has expired');
    end if;
    if u.state <> 'finalized' then
      perform infrx.lifecycle_refuse('upload_not_finalized', 'upload ' || u.handle || ' is '
                                     || u.state || ', not finalized');
    end if;
  end if;
  select * into c from infrx.content_objects
   where location = 'object_store' and object_key = m->>'storage_ref' for share;
  if not found or c.org_id <> j.org_id or c.kind <> 'source' or c.state = 'deleted' then
    perform infrx.lifecycle_refuse('not_found', 'no live source ' || coalesce(m->>'handle', '')
                                   || ' of this organization');
  end if;
  if c.state = 'tombstoned' then
    perform infrx.lifecycle_refuse('content_retiring', 'source ' || (m->>'handle')
                                   || ' is being deleted; stage it again');
  end if;
  if c.digest is distinct from m->>'digest'
     or (c.bytes is not null and c.bytes <> (m->>'bytes')::bigint)
     or (m->>'kind' = 'upload' and (u.source_content_id, u.source_generation)
         is distinct from (c.content_id, c.generation)) then
    perform infrx.lifecycle_refuse('not_found', 'source ' || coalesce(m->>'handle', '')
                                   || ' does not name that content');
  end if;
  insert into infrx.staged_media (org_id, handle, kind, state, digest, bytes, mime,
    storage_ref, profile_version, duration_s, finalized_at)
  values (j.org_id, m->>'handle', m->>'kind', 'finalized', m->>'digest',
    (m->>'bytes')::bigint, m->>'mime', m->>'storage_ref',
    coalesce(m->>'profile_version', 'v1'), (m->>'duration_s')::float8, v_now)
  on conflict (org_id, handle) do nothing;
  select digest into v_recorded from infrx.staged_media
   where org_id = j.org_id and handle = m->>'handle';
  if v_recorded is distinct from m->>'digest' then
    perform infrx.lifecycle_refuse('bytes_changed', 'handle ' || (m->>'handle')
                                   || ' already names other content');
  end if;
  insert into infrx.job_media (job_id, org_id, handle, role, position, content_id, generation)
  values (j.request_id, j.org_id, m->>'handle', 'source', p_position, c.content_id,
          c.generation);
end $$;

-- The pinned serving revision serves what the request needs (`catalog.check_capability`,
-- against the PIN, never what an alias resolves to now). A CREDIT job pins its serving
-- revision; a legacy USD job pins its `model_revision` (`<alias>@<label>`, canonical since
-- P-22), whose serving revision is the one that label names - a pre-catalog model has none
-- and no capability record to check (the ingress's check is all it ever had).
create or replace function infrx.check_pinned_capability(j infrx.jobs, r jsonb) returns void
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_cap jsonb;
begin
  if j.accounting_regime = 'credit' then
    select s.capability into v_cap from infrx.serving_versions s
     where s.serving_version_id = j.serving_version_id;
  else
    select s.capability into v_cap from infrx.serving_versions s
      join public.models m on m.model_uuid = s.model_id
     where m.id = split_part(j.model_revision, '@', 1)
       and s.revision_label = nullif(split_part(j.model_revision, '@', 2), '');
    if v_cap is null then
      return;
    end if;
  end if;
  if v_cap is null then
    perform infrx.refuse('not_found', 'the pinned serving revision is not in the catalog');
  end if;
  if (jsonb_array_length(coalesce(r->'media', '[]')) > 0
      and not coalesce(v_cap->'input_modalities' ? 'video', false)) or exists (
      select 1 from jsonb_array_elements(coalesce(r->'messages', '[]')) msg
        left join lateral jsonb_array_elements(case jsonb_typeof(msg->'content')
                    when 'array' then msg->'content' else '[]' end) part on true
       where case when jsonb_typeof(msg->'content') = 'string' then 'text'
                  when part is null then null
                  when part->>'type' = 'video_url' then 'video'
                  else part->>'type' end
             not in (select jsonb_array_elements_text(coalesce(v_cap->'input_modalities',
                                                               '[]')))) then
    raise exception using errcode = 'P0001', hint = 'param=messages',
      message = 'unsupported_media: the model does not accept this input modality';
  end if;
  if j.execution_mode = 'stream' and v_cap->>'stream_output' is distinct from 'true' then
    raise exception using errcode = 'P0001', hint = 'param=stream',
      message = 'unsupported_parameter: the model does not stream its output';
  end if;
end $$;

-- `ReadinessStore.admit_ready`. Args: `admit`'s `{regime, request, idem, limits, budgets}`
-- plus `expectation` `{accounting_regime, rate_card_version}` - the RUNTIME's configuration,
-- never a request field. Answers `{admission, readiness}`; a replay answers the recorded
-- pair (a job the previous runtime admitted has `readiness: null`).
create or replace function infrx.admit_ready(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  x jsonb := p_args->'expectation';
  v_doc jsonb;
  v_media jsonb := coalesce(p_args->'request'->'media', '[]');
  v_i int := 0;
  m jsonb;
  j infrx.jobs%rowtype;
begin
  if jsonb_typeof(x) is distinct from 'object' or jsonb_typeof(v_media) <> 'array'
     or (p_args->>'retention_s')::float8 is null or (p_args->>'retention_s')::float8 < 0 then
    perform infrx.refuse('invalid_request', 'admit_ready takes admit''s arguments, an '
                         || 'expectation and retention_s');
  end if;
  if p_args->'idem'->>'org_id' is distinct from p_args->'request'->>'org_id' then
    perform infrx.refuse('forbidden', 'the idempotency scope must name the request''s org');
  end if;
  -- R91: a replay answers the recorded pair before anything is checked again (the key's
  -- first admission was checked; a card that moved since does not refuse its replay).
  v_doc := infrx.admission_replay(p_args->'idem',
             (p_args->'limits'->>'idempotency_ttl_s')::float8, infrx.now(), p_args->>'regime');
  if v_doc is not null then
    return jsonb_build_object('admission', v_doc,
                              'readiness', infrx.readiness_doc((v_doc->>'request_id')::uuid));
  end if;
  -- R69: the regime and card this deployment serves. A CREDIT expectation names its card;
  -- a legacy one names none.
  if x->>'accounting_regime' is distinct from p_args->>'regime'
     or (p_args->>'regime' = 'credit') = (coalesce(x->>'rate_card_version', '') = '') then
    perform infrx.lifecycle_refuse('expectation_mismatch',
      'the runtime''s expectation does not name this regime and card');
  end if;
  -- The manifest is the request's own organization's (R55) and names each source once
  -- (checked before anything is locked or written).
  if exists (select 1 from jsonb_array_elements(v_media) e
              where e->>'org_id' is distinct from p_args->'request'->>'org_id') then
    perform infrx.lifecycle_refuse('not_found', 'a request may only carry its own org''s media');
  end if;
  if (select count(distinct e->>'handle') from jsonb_array_elements(v_media) e)
     <> jsonb_array_length(v_media) then
    perform infrx.lifecycle_refuse('invalid_manifest', 'a manifest names each source once');
  end if;
  v_doc := infrx.admit(p_args);
  if (v_doc->>'replayed')::boolean then
    return jsonb_build_object('admission', v_doc,
                              'readiness', infrx.readiness_doc((v_doc->>'request_id')::uuid));
  end if;
  select * into j from infrx.jobs where request_id = (v_doc->>'request_id')::uuid;
  if j.accounting_regime = 'credit'
     and j.rate_card_version is distinct from x->>'rate_card_version' then
    perform infrx.lifecycle_refuse('expectation_mismatch',
      'the model is not priced for this deployment');
  end if;
  perform infrx.check_pinned_capability(j, p_args->'request');
  for m in select value from jsonb_array_elements(v_media) loop
    perform infrx.bind_source(j, m, v_i);
    v_i := v_i + 1;
  end loop;
  insert into infrx.job_readiness (job_id, org_id, ready_at, source_count, retention_s)
  values (j.request_id, j.org_id, j.admitted_at, v_i, (p_args->>'retention_s')::float8);
  return jsonb_build_object('admission', v_doc,
                            'readiness', infrx.readiness_doc(j.request_id));
end $$;

-- `ReadinessStore.claim_preparation`: 0012's claim behind the marker. The job row is locked
-- first (0012's own lock, re-entrant), and a preparing job inside its preparation phase
-- without a marker is `not_ready`; everything else - unknown, terminal, not preparing, past
-- its deadline (R29 terminalizes it) - is 0012's answer. The marker is written only in the
-- admission transaction, so it cannot appear for a job that exists without one.
create or replace function infrx.claim_preparation_ready(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.jobs%rowtype;
begin
  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid for update;
  if found and j.state = 'preparing' and j.settled_at is null
     and infrx.now() < j.preparation_deadline_at
     and not exists (select 1 from infrx.job_readiness r where r.job_id = j.request_id) then
    perform infrx.lifecycle_refuse('not_ready', 'job ' || j.request_id
                                   || ' has no execution-ready marker');
  end if;
  return infrx.claim_preparation(p_args);
end $$;

-- The operator's cutover gate (I8): `ready` exactly when admission is paused in both
-- regimes and no preparing job lacks a marker - only then may the previous runtime's
-- worker stop (the new one never prepares such a job). Read-only.
create or replace function infrx.readiness_cutover_check() returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  with waiting as (
    select j.request_id, j.preparation_deadline_at from infrx.jobs j
     where j.state = 'preparing'
       and not exists (select 1 from infrx.job_readiness r where r.job_id = j.request_id)),
  flags as (
    select coalesce(bool_or(f.enabled) filter (where f.name in ('credit_admission',
                                                                 'legacy_usd_admission')),
                    false) as admitting
      from infrx.feature_flags f)
  select jsonb_build_object(
    'ready', not flags.admitting and not exists (select 1 from waiting),
    'admission_paused', not flags.admitting,
    'unmarked_preparing', (select count(*) from waiting),
    'last_deadline', (select max(preparation_deadline_at) from waiting),
    -- Not a gate: the doors that write no marker which the dedicated runtime login (0021)
    -- still holds. The barrier is the new runtime CALLING `admit_ready` /
    -- `claim_preparation_ready` (G7, W5); once both are wired these are revoked from it
    -- (the previous runtime keeps them through service_role) and this answers [].
    'runtime_unmarked_doors', coalesce((select jsonb_agg(d order by d) from unnest(array[
        'infrx.admit(jsonb)', 'infrx.claim_preparation(jsonb)']) d
       where case when exists (select 1 from pg_roles where rolname = 'infrx_runtime')
                  then has_function_privilege('infrx_runtime', d, 'execute') end), '[]'))
  from flags;
$$;

-- ================================================================ privileges ===
alter table infrx.content_objects enable row level security;
alter table infrx.job_readiness enable row level security;
revoke all on infrx.content_objects, infrx.job_readiness
  from public, anon, authenticated, service_role;
-- The platform reads them; every write is a SECURITY DEFINER boundary below.
grant select on infrx.content_objects, infrx.job_readiness to service_role;
-- 0010 let the platform write tickets directly (M3's planned UPDATE); the writer is now the
-- `upload_*` boundary, so a direct write - which would skip its tenant and window rules -
-- is taken back. Reads stay.
revoke insert, update, delete on infrx.media_uploads from service_role;
do $$
declare
  f text;
begin
  foreach f in array array[
      'infrx.lifecycle_code(text)',
      'infrx.lifecycle_refuse(text, text)', 'infrx.lifecycle_refusal(text, text)',
      'infrx.content_objects_guard()', 'infrx.content_doc(infrx.content_objects)',
      'infrx.register_content(jsonb, double precision)',
      'infrx.upload_doc(infrx.media_uploads)', 'infrx.upload_row(uuid, text, text)',
      'infrx.bind_source(infrx.jobs, jsonb, integer)',
      'infrx.check_pinned_capability(infrx.jobs, jsonb)', 'infrx.media_uploads_guard()']
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role',
                   f);
  end loop;
  foreach f in array array[
      'infrx.content_register(jsonb)', 'infrx.upload_create(jsonb)',
      'infrx.upload_acknowledge_put(jsonb)', 'infrx.upload_complete(jsonb)',
      'infrx.upload_abort(jsonb)', 'infrx.upload_resolve(jsonb)', 'infrx.upload_expire(jsonb)',
      'infrx.readiness_doc(uuid)', 'infrx.admit_ready(jsonb)',
      'infrx.claim_preparation_ready(jsonb)', 'infrx.readiness_cutover_check()',
      'infrx.content_references(jsonb)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
end $$;
comment on function infrx.admit_ready(jsonb) is
  'D10 (0019): ReadinessStore.admit_ready - admission, the runtime''s expectation, the '
  'pinned capability, the manifest and the execution-ready marker in one transaction.';
comment on function infrx.claim_preparation_ready(jsonb) is
  'D10 (0019): ReadinessStore.claim_preparation - no preparation lease without the marker.';
