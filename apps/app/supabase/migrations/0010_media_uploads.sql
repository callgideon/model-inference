-- D2 item 5: durable upload rows and media last-use (M3 integration request 1, D1R
-- evidence integration request 3, verbatim where it is specific).
--
--   infrx.media_uploads   one row per customer upload handle; the finalize-once statement
--                         M3 runs is
--                           UPDATE infrx.media_uploads SET state='finalized', ...
--                            WHERE org_id=$1 AND handle=$2 AND state='created'
--                              AND infrx.now() < expires_at RETURNING *
--                         which replaces M3's in-process lock across processes.
--   infrx.media_objects   (storage_ref, org_id, last_used_at): the durable `idle_since`.
--                         `touch_media_object` stamps a use; `delete_media_object_if_idle`
--                         is the collector's CONDITIONAL delete - it removes the row only if
--                         `last_used_at` is still the value the collector observed, so a
--                         stage that re-used the object meanwhile wins the race (M3 limit 2).
--
-- Tenant: every lookup is `(org_id, handle)`; a stamp on an object of another
-- organization is refused. RLS is on with no browser policy (schema `infrx` is closed to
-- browser roles anyway, 0004); the platform role reads/writes, never truncates.
-- Timestamps are `infrx.now()` (R7). Additive and re-runnable.

create table if not exists infrx.media_uploads (
  handle text primary key check (handle ~ '^upl_[A-Za-z0-9_-]{22,64}$'),
  org_id uuid not null references public.organizations(id) on delete cascade,
  state text not null check (state in ('created', 'finalized', 'aborted', 'expired')),
  -- MAX_MEDIA_BYTES' frozen default (64 MiB); raising the setting needs this bound moved.
  max_bytes bigint not null check (max_bytes between 1 and 67108864),
  declared_bytes bigint check (declared_bytes >= 1),
  declared_digest text check (declared_digest ~ '^sha256:[0-9a-f]{64}$'),
  accepted_mime text[] not null check (cardinality(accepted_mime) > 0
                                       and array_position(accepted_mime, null) is null),
  created_at timestamptz not null default infrx.now(),
  expires_at timestamptz not null,
  finalized_at timestamptz,
  -- finalized facts
  digest text check (digest ~ '^sha256:[0-9a-f]{64}$'),
  bytes bigint check (bytes >= 0),
  mime text,
  duration_s double precision check (duration_s >= 0),
  storage_ref text,
  profile_version text,
  aborted_reason text check (length(aborted_reason) <= 500),
  constraint media_uploads_org_handle_key unique (org_id, handle),
  constraint media_uploads_expires_after_created check (expires_at > created_at),
  constraint media_uploads_finalized_facts check (
    (state = 'finalized') = (finalized_at is not null) and
    (state = 'finalized') = (num_nonnulls(digest, bytes, mime, duration_s, storage_ref,
                                          profile_version) = 6) and
    (state = 'finalized' or num_nonnulls(digest, bytes, mime, duration_s, storage_ref,
                                         profile_version) = 0)),
  constraint media_uploads_bytes_within_max check (bytes is null or bytes <= max_bytes),
  constraint media_uploads_abort_reason_only_when_aborted
    check (aborted_reason is null or state = 'aborted')
);
create index if not exists media_uploads_state_expires_idx
  on infrx.media_uploads (state, expires_at);

-- Identity never changes; only `created` moves (to finalized, aborted or expired), and a
-- finalized upload's content is as immutable as the object it names.
create or replace function infrx.media_uploads_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    -- A finalized upload is the record `resolve_owned` answers from (R82): it goes only
    -- once its window has passed.
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
create or replace trigger media_uploads_guard before update or delete on infrx.media_uploads
  for each row execute function infrx.media_uploads_guard();

create table if not exists infrx.media_objects (
  storage_ref text primary key check (length(storage_ref) between 1 and 1024),
  org_id uuid not null references public.organizations(id) on delete cascade,
  last_used_at timestamptz not null default infrx.now()
);
create index if not exists media_objects_last_used_idx on infrx.media_objects (last_used_at);

-- A use of an object: insert it or move `last_used_at` forward, for its OWN organization.
create or replace function infrx.touch_media_object(p_storage_ref text, p_org uuid)
returns timestamptz language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_at timestamptz;
begin
  insert into infrx.media_objects (storage_ref, org_id, last_used_at)
  values (p_storage_ref, p_org, infrx.now())
  on conflict (storage_ref) do update set last_used_at = greatest(
      infrx.media_objects.last_used_at, excluded.last_used_at)
    where infrx.media_objects.org_id = excluded.org_id
  returning last_used_at into v_at;
  if v_at is null then
    raise exception 'not_found: object %', p_storage_ref using errcode = 'P0002';
  end if;
  return v_at;
end $$;

-- The collector's conditional delete: true only when the row still carries the
-- `last_used_at` the collector observed (nobody used it since) - then the object may go.
create or replace function infrx.delete_media_object_if_idle(p_storage_ref text,
                                                             p_observed timestamptz)
returns boolean language sql security definer set search_path = infrx, public, pg_temp as $$
  with gone as (delete from infrx.media_objects
                 where storage_ref = p_storage_ref and last_used_at = p_observed
                returning 1)
  select exists (select 1 from gone);
$$;

alter table infrx.media_uploads enable row level security;
alter table infrx.media_objects enable row level security;
revoke all on infrx.media_uploads, infrx.media_objects
  from public, anon, authenticated, service_role;
grant select, insert, update, delete on infrx.media_uploads to service_role;
grant select, insert, update, delete on infrx.media_objects to service_role;
revoke all on function infrx.media_uploads_guard() from public, anon, authenticated, service_role;
revoke all on function infrx.touch_media_object(text, uuid) from public, anon, authenticated;
revoke all on function infrx.delete_media_object_if_idle(text, timestamptz)
  from public, anon, authenticated;
grant execute on function infrx.touch_media_object(text, uuid) to service_role;
grant execute on function infrx.delete_media_object_if_idle(text, timestamptz) to service_role;
