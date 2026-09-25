-- D10 follow-up: the fenced end of a permanently refused preparation (W5 wiring request 3,
-- R104's third candidate) and the operator's fair, bounded flag writer (G8 V-G8TL-2).
--
--   fail_preparation     `{lease, cause, limits}` -> the committed outcome. A preparation
--                        worker calls it only for a PERMANENT refusal (W5's ruling text:
--                        unsupported_media / request_too_large -> invalid_media,
--                        context_length_exceeded -> preparation_failed; every other refusal
--                        lapses its lease). The cause is checked first (anything else is
--                        invalid_request, nothing read). Then the replay: the SAME lease's
--                        identical call after it committed answers the committed outcome.
--                        Then 0016's fence on the PREPARATION lease (kind, terminal, live
--                        attempt, generation, owner, R29 deadline - terminalized and
--                        answered as data, R39 - then expiry). Then 0016's
--                        `terminalize_no_usage` (state `failed`, no usage, released_free by
--                        R21, the hold released in its own unit, every reservation and the
--                        attempt released, one usage and one trace projection; 0017's
--                        trigger writes the terminal journal event). A lapsed, foreign or
--                        superseded lease is stale_lease; a job another path ended first is
--                        already_terminal; neither changes anything.
--   set_feature_flag     V-G8TL-2 option A: `lock table infrx.feature_flags in exclusive mode`,
--                        then 0006's attributed UPDATE, returning whether it changed the row.
--                        EXCLUSIVE conflicts with the ROW SHARE every admission's
--                        `require_feature ... for share` (0021) takes and still admits plain
--                        reads; a waiting table lock queues NEW share lockers behind it, so a
--                        stream of overlapping admissions can no longer starve the freeze
--                        (a row-level UPDATE joined the MultiXact queue last). SHARE ROW
--                        EXCLUSIVE would not: it does not conflict with ROW SHARE. The
--                        caller's SET LOCAL lock_timeout / statement_timeout bound it.
--
--   register_content     (M6 WR-7) 0019's body plus one rule: a `written` registration that
--   content_objects_guard finds a LIVE row at the same (location, key) with the same digest
--                        sets `eligible_at = greatest(eligible_at, now + grace)` under the
--                        row lock; a `discovered` one never does. The guard now lets a live
--                        generation's eligibility move later, never earlier.
--
-- RETIRED (M6 WR-8). 0019's comment on `register_content` says a deleted key's next
-- generation starts "not before the claim that deleted it has lapsed". No migration ever
-- implemented that, and none will. R129 and the amended R114 decide the delete instead:
-- a tombstoned key refuses registration (`content_retiring`) until its delete is
-- acknowledged; 0020's `content_acknowledge_delete` accepts only the tombstone's current
-- claim fence (a superseded claim's ack is refused), and the collector calls it only after
-- the store's delete returned; the delete is issued only while the claim has at least one
-- store request timeout left. Writers write bare keys, so every generation reuses the same
-- physical key (`key.g<n>` names no object).
-- The residual is R129's, named here: a delete the client abandoned at its timeout, which
-- the store executes only after a later claim's acknowledged delete and a re-registration's
-- write at the same key, removes that new generation's bytes. Waiting for the deleting
-- claim to lapse would not close it (it adds one claim TTL to the delay such a request
-- needs, and bounds nothing on the store's side) and would refuse a legitimate
-- re-registration for up to a claim TTL. Generation-keyed physical names are R129's
-- upgrade (M6 writers, `MediaRef`, D10 key matching). The sentence is void from 0022 on.
--
--   jobs_result_expiry_guard (L3-REBASE F2) revoked from everyone, like its sibling guards.
--
-- The replay key. `jobs.proposal` (0018: the winner's proposal, immutable once settled) is
-- written with the lease's generation and worker BEFORE the terminalization, so only the
-- identical call of the lease that ended the job replays; it can never equal a
-- `terminalize` proposal (that has no `fail_preparation` member).
--
-- LOCK ORDER. `fail_preparation`: the job row (the replay read and the fence take it FOR
-- UPDATE), then its hold, then its wallet - 0016's order. `set_feature_flag`: only the
-- flags table; an admission takes the flag after its scope lock, so no cycle exists (0021).
--
-- ROLLBACK (0022 alone). `drop function infrx.fail_preparation(jsonb)` and
-- `drop function infrx.set_feature_flag(text, boolean, text, text)`; the worker then lapses
-- a permanently refused lease (W5's fallback) and G8 writes the row directly (V-G8TL-1).
-- `grant execute on function infrx.jobs_result_expiry_guard() to service_role` restores
-- 0021's (unused) grant. Re-run 0019's `content_objects_guard` and `register_content` (the
-- refetch race returns; eligibilities already moved later stay later - retaining longer is
-- the safe direction).
-- Jobs already ended keep their outcome: money history is never un-settled.
--
-- Additive and re-runnable.

-- ======================================================= fail_preparation ===
create or replace function infrx.fail_preparation(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_lease jsonb := p_args->'lease';
  v_cause text := p_args->>'cause';
  v_mark jsonb;
  v_refusal jsonb;
  v_reconcile_s float8;
  j infrx.jobs%rowtype;
begin
  if jsonb_typeof(v_lease) is distinct from 'object' or v_lease->>'job_id' is null then
    perform infrx.refuse('invalid_request', 'fail_preparation takes {lease, cause, limits}');
  end if;
  if v_cause is null or v_cause not in ('invalid_media', 'preparation_failed') then
    perform infrx.refuse('invalid_request', coalesce(v_cause, 'null')
                         || ' does not end a preparation');
  end if;
  v_reconcile_s := infrx.lease_limit(p_args, 'unknown_usage_reconcile_s');
  v_mark := jsonb_build_object('cause', v_cause, 'usage', null, 'result_ref', null,
                               'fail_preparation', jsonb_build_object(
                                 'generation', v_lease->'generation',
                                 'worker_id', v_lease->'worker_id'));
  -- The replay: this lease's identical call, after it committed.
  select * into j from infrx.jobs where request_id = (v_lease->>'job_id')::uuid for update;
  if found and j.settled_at is not null and j.proposal = v_mark then
    return infrx.job_admission(j.request_id);
  end if;
  v_refusal := infrx.fence_lease(v_lease, array['preparation'], v_reconcile_s);
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  update infrx.jobs set proposal = v_mark where request_id = j.request_id;
  perform infrx.terminalize_no_usage(j.request_id, v_cause, 'failed', v_reconcile_s);
  return infrx.job_admission(j.request_id);
end $$;

revoke all on function infrx.fail_preparation(jsonb) from public, anon, authenticated;
grant execute on function infrx.fail_preparation(jsonb) to service_role, infrx_runtime;

-- ======================================================= set_feature_flag ===
create or replace function infrx.set_feature_flag(p_name text, p_enabled boolean,
                                                  p_actor text, p_reason text)
returns boolean language plpgsql volatile security definer
set search_path = infrx, public, pg_temp as $$
begin
  lock table infrx.feature_flags in exclusive mode;
  update infrx.feature_flags
     set enabled = p_enabled, updated_by = left(p_actor, 200), reason = left(p_reason, 500),
         updated_at = infrx.now()
   where name = p_name and enabled <> p_enabled;
  return found;
end $$;

revoke all on function infrx.set_feature_flag(text, boolean, text, text)
  from public, anon, authenticated;
grant execute on function infrx.set_feature_flag(text, boolean, text, text) to service_role;

-- ================================================ written re-registration (WR-7) ===
-- 0019's guard and `register_content`, each with ONE change (marked `0022`): a `written`
-- registration that finds a LIVE row at the same key with the same digest moves its
-- `eligible_at` to greatest(eligible_at, now + grace) under the row lock. Without it, a clip
-- fetched again after its first row became eligible got the old row back and a collector
-- pass before the second request's admission deleted the bytes just fetched (M6 fix round
-- R2/A1). A `discovered` registration never refreshes; eligibility never moves earlier.
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
     -- 0022 (M6 WR-7): a live generation's eligibility may only move LATER (a written
     -- re-registration of the same bytes); never earlier, never on a retiring row.
     or (new.eligible_at is distinct from old.eligible_at
         and not (old.state = 'live' and new.state = 'live'
                  and new.eligible_at > old.eligible_at)) then
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
  -- 0022 (M6 WR-7): the runtime is about to (re)write these same bytes for a new request, so
  -- the row's grace restarts from now - under the row lock taken above, so a collector's
  -- claim/tombstone recheck (0020 `content_recheck`) sees it. A `discovered` registration
  -- never does: finding an object is not a reason to keep it longer.
  if p_identity->>'origin' = 'written' and c.state = 'live'
     and c.digest = p_identity->>'digest' then
    update infrx.content_objects
       set eligible_at = greatest(eligible_at, v_now + make_interval(secs => p_grace_s))
     where content_id = c.content_id
    returning * into c;
  end if;
  return c;
end $$;

-- ============================================================ privileges ===
-- L3-REBASE F2: 0021's trigger guard kept 0004:53's default EXECUTE for service_role; its
-- siblings (0019 `content_objects_guard`, `media_uploads_guard`, 0020 `job_results_guard`)
-- are revoked from everyone. A trigger function needs no EXECUTE to fire.
revoke all on function infrx.jobs_result_expiry_guard()
  from public, anon, authenticated, service_role;
