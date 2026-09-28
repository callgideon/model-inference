-- C2-RPC (wave-5 LW3, lane lab-sql; the content lane's WR-C2-1 / WR-C2-SQL-1, C2 evidence
-- research/plan/evidence/c/C2-ae7375e.md): the ONE content-access rule set - short-lived refs
-- to one request's trace content, bound to a grant, the recipient provider, the issuing user
-- and an expiry, every rule re-checked on the database clock at every read. C2's Python
-- adapter (`infrx/state/lab_content.py` PgContentRefs = `infrx.content.ContentRefs`), G4T, N3
-- and the Lab's TS adapter (`apps/lab/lib/services/content/refs.ts`) all call it; the
-- executable contract is the content lane's `infrx/content/fakes.py` FakeContentRefs.
-- Also N3's optional request (datasets-lw4, N3-39a64c3.md): sample tombstones and content
-- bounds, so D7 answers "permitted now" itself.
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
--   lab_content_refs      one row per issued handle (only its SHA-256; the handle never
--                         reaches the database): grant, grantor, recipient provider, user,
--                         request, purpose, categories, issued/expiry. Append-only.
--   lab_sample_tombstones one per (provider, sample id), permanent (a re-grant never
--                         resurrects; the first reason stands). Append-only.
--   lab_sample_bounds     one content bound per (provider, sample id) (T3's content_until),
--                         write-once. Append-only.
--   (N3 asked for `tombstoned_at/reason/content_until` COLUMNS on lab_dataset_samples; that
--   table is immutable (0029, DATA-IMMUTABLE) and a sample id is shared by derived versions,
--   so they are these two sibling tables keyed by (provider, sample id) instead - one row
--   covers every version that carries the sample.)
--
-- The rights (`lab_content_rights`, the fake's `_current`, `may_read_customer_content`):
-- the pair's CURRENT grant version (R166) in force at infrx.now() (effective, not revoked,
-- not expired), naming the job's model (`infrx.jobs.model_id` of the grantor's own job -
-- never the caller's word), every category and the purpose; the user a CURRENT developer or
-- administrator of the provider; the retention bound = the job's created_at + the current
-- grant's retention_days. Default deny: anything missing or NULL is no row.
--
-- Named RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults; jsonb in
-- and out; refusals `<code>: detail`):
--   lab_content_ref_issue {handle_sha256, user_id, provider_org_id, grant_ref, request_id,
--       purpose, categories, ttl_s} -> {grant_id, grant_version (CURRENT), grantor_org_id,
--       request_id, purpose, expires_at}: invalid_request (ttl outside 1..900, no or unknown
--       category, unknown purpose, malformed digest or id) -> not_found (the ref is not a
--       version of a grant TO this provider, or the request is not the grantor's job) ->
--       forbidden (the rights) -> result_expired (least(now + ttl, the grant's expiry, the
--       retention bound) <= now) -> state_conflict (the digest is already issued).
--   lab_content_ref_redeem {handle_sha256, user_id, provider_org_id} -> the same shape with
--       expires_at = least(the ref's expiry, the retention bound): not_found (no row, or
--       another provider or user) -> result_expired (the ref's expiry) -> forbidden (the
--       rights, with the ref's purpose and categories) -> result_expired (retention).
--   lab_content_ref_held {org_id, request_id} -> {held}: a ref of that request, unexpired,
--       whose rights and retention still hold (T3's `Retention(holds=...)`).
--   lab_tombstone_samples {provider_org_id, sample_ids, reason} -> {tombstoned: the ids
--       this call stoned}; the first reason stands.
--   lab_bound_samples {provider_org_id, bounds: [{sample_id, content_until}]} -> {bounded}:
--       write-once; the same bound replays, another is idempotency_conflict.
--   lab_blocked_samples {provider_org_id, dataset_ref} -> {sample_id: reason} of the
--       dataset's samples: the tombstone's reason, or `content_expired` once
--       infrx.now() >= content_until (N3's `blocked`).
--   lab_permitted_samples {provider_org_id, dataset_ref, purpose?} -> [sample_id]: D7's
--       lab_accessible_samples minus the blocked (N3's `permitted`).
-- The session door (R171; the Lab holds no service key; PostgREST exposes `public` only):
--   public.lab_content_ref_issue(p_handle_sha256, p_provider_org_id, p_grant_ref,
--       p_request_id, p_purpose): the user is auth.uid(), the categories request_content +
--       response_content (R47: a trace body carries both), ttl 300 -> lab_content_ref_issue's
--       answer and refusals. Gated by its own flag `lab_content` (OFF; a missing row is as
--       closed as a disabled one: 55000, which the Lab shows as "unavailable"). No redeem
--       door: content is read by the platform's content service only.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   public.lab_content_ref_issue(text, uuid, text, uuid, text),
--   infrx.lab_permitted_samples(jsonb), infrx.lab_blocked_samples(jsonb),
--   infrx.lab_bound_samples(jsonb), infrx.lab_tombstone_samples(jsonb),
--   infrx.lab_content_ref_held(jsonb), infrx.lab_content_ref_redeem(jsonb),
--   infrx.lab_content_ref_issue(jsonb), infrx.lab_content_ref_json(infrx.lab_content_refs,
--   timestamptz, int), infrx.lab_content_rights(uuid, uuid, uuid, uuid, text, text[]);
--   drop table infrx.lab_sample_bounds, infrx.lab_sample_tombstones, infrx.lab_content_refs;
--   delete the `lab_content` flag row if any, then restore feature_flags_name_check without
--   'lab_content' (0031's list).
--
-- Re-runnable: `if not exists`, `create or replace`, `drop ... if exists` before each re-add.

alter table infrx.feature_flags drop constraint if exists feature_flags_name_check;
alter table infrx.feature_flags add constraint feature_flags_name_check
  check (name in ('signup_grant', 'credit_admission', 'legacy_usd_admission', 'feedback',
                  'lab_submission', 'lab_content'));

-- ==================================================================== tables ===
create table if not exists infrx.lab_content_refs (
  handle_sha256 text primary key check (handle_sha256 ~ '^[0-9a-f]{64}$'),
  grant_id uuid not null,
  grantor_org_id uuid not null references public.organizations(id) on delete restrict,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  user_id uuid not null references public.profiles(id) on delete restrict,
  request_id uuid not null,
  purpose text not null
    check (purpose in ('capture', 'provider_sharing', 'external_judging', 'training')),
  categories text[] not null check (cardinality(categories) > 0 and categories <@ array[
    'request_content', 'response_content', 'media', 'usage_metadata', 'feedback']),
  issued_at timestamptz not null default infrx.now(),
  expires_at timestamptz not null,
  constraint lab_content_refs_expire_after_issue check (expires_at > issued_at)
);
create index if not exists lab_content_refs_by_request
  on infrx.lab_content_refs (grantor_org_id, request_id, expires_at);

create table if not exists infrx.lab_sample_tombstones (
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  sample_id uuid not null,
  reason text not null check (reason ~ '^[a-z][a-z_]{0,63}$'),
  tombstoned_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, sample_id)
);

create table if not exists infrx.lab_sample_bounds (
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  sample_id uuid not null,
  content_until timestamptz not null,
  primary key (provider_org_id, sample_id)
);

do $$
declare
  t text;
begin
  foreach t in array array['lab_content_refs', 'lab_sample_tombstones', 'lab_sample_bounds']
  loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ==================================================================== rights ===
-- One row when every rule holds on the CURRENT rows at infrx.now(), else none.
create or replace function infrx.lab_content_rights(p_grantor uuid, p_provider uuid,
  p_user uuid, p_request uuid, p_purpose text, p_categories text[])
returns table (grant_id uuid, grant_version int, grant_expires_at timestamptz,
               retained_until timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select g.grant_id, g.version, g.expires_at,
         j.created_at + make_interval(days => g.retention_days)
    from (select * from infrx.lab_access_grants x
           where x.grantor_org_id = p_grantor and x.recipient_provider_org_id = p_provider
           order by x.version desc limit 1) g
    join infrx.jobs j on j.org_id = p_grantor and j.request_id = p_request
   where g.effective_at <= infrx.now()
     and (g.revoked_at is null or infrx.now() < g.revoked_at)
     and (g.expires_at is null or infrx.now() < g.expires_at)
     and j.model_id::text = any(g.model_ids)
     and cardinality(p_categories) > 0 and p_categories <@ g.categories
     and p_purpose = any(g.purposes)
     and exists (select 1 from infrx.provider_memberships m
                  where m.provider_org_id = p_provider and m.user_id = p_user
                    and m.role in ('developer', 'administrator')
                    and m.granted_at <= infrx.now()
                    and (m.revoked_at is null or infrx.now() < m.revoked_at))
$$;

create or replace function infrx.lab_content_ref_json(c infrx.lab_content_refs,
  p_expires_at timestamptz, p_grant_version int)
returns jsonb language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('grant_id', c.grant_id, 'grant_version', p_grant_version,
    'grantor_org_id', c.grantor_org_id, 'request_id', c.request_id, 'purpose', c.purpose,
    'expires_at', p_expires_at)
$$;

-- ====================================================================== refs ===
create or replace function infrx.lab_content_ref_issue(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid;
  v_user uuid;
  v_request uuid;
  v_ttl int;
  v_cats text[];
  v_purpose text := p_args->>'purpose';
  pinned infrx.lab_access_grants%rowtype;
  r record;
  v_expires timestamptz;
  c infrx.lab_content_refs%rowtype;
begin
  begin
    v_provider := (p_args->>'provider_org_id')::uuid;
    v_user := (p_args->>'user_id')::uuid;
    v_request := (p_args->>'request_id')::uuid;
    v_ttl := (p_args->>'ttl_s')::int;
    v_cats := array(select jsonb_array_elements_text(p_args->'categories'));
  exception when invalid_text_representation or numeric_value_out_of_range
                 or invalid_parameter_value then
    perform infrx.refuse('invalid_request', 'a content ref names ids, categories and a ttl');
  end;
  if v_ttl is null or v_ttl not between 1 and 900 or cardinality(v_cats) = 0
     or not v_cats <@ array['request_content', 'response_content', 'media', 'usage_metadata',
                            'feedback']
     or v_purpose is null
     or v_purpose <> all (array['capture', 'provider_sharing', 'external_judging', 'training'])
     or coalesce(p_args->>'handle_sha256', '') !~ '^[0-9a-f]{64}$' then
    perform infrx.refuse('invalid_request', 'a content ref needs a bounded ttl (1..900 s), '
                         'a category, a purpose and a handle digest');
  end if;
  select * into pinned from infrx.lab_grant_of(p_args->>'grant_ref', v_provider);
  if pinned.grant_id is null or not exists (
       select 1 from infrx.jobs j where j.org_id = pinned.grantor_org_id
          and j.request_id = v_request) then
    perform infrx.refuse('not_found', 'no such request under a grant to this provider');
  end if;
  select * into r from infrx.lab_content_rights(pinned.grantor_org_id, v_provider, v_user,
                                                v_request, v_purpose, v_cats);
  if not found then
    perform infrx.refuse('forbidden', 'no current access grant for this provider, request, '
                         'category and purpose');
  end if;
  v_expires := least(infrx.now() + make_interval(secs => v_ttl), r.retained_until,
                     r.grant_expires_at);
  if v_expires <= infrx.now() then
    perform infrx.refuse('result_expired', 'the content is past its grant''s retention');
  end if;
  insert into infrx.lab_content_refs (handle_sha256, grant_id, grantor_org_id,
    provider_org_id, user_id, request_id, purpose, categories, expires_at)
  values (p_args->>'handle_sha256', r.grant_id, pinned.grantor_org_id, v_provider, v_user,
    v_request, v_purpose, v_cats, v_expires)
  on conflict (handle_sha256) do nothing
  returning * into c;
  if c.handle_sha256 is null then
    perform infrx.refuse('state_conflict', 'this handle is already issued');
  end if;
  return infrx.lab_content_ref_json(c, v_expires, r.grant_version);
end $$;

create or replace function infrx.lab_content_ref_redeem(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  c infrx.lab_content_refs%rowtype;
  r record;
begin
  select * into c from infrx.lab_content_refs where handle_sha256 = p_args->>'handle_sha256';
  if not found or c.provider_org_id::text is distinct from p_args->>'provider_org_id'
     or c.user_id::text is distinct from p_args->>'user_id' then
    perform infrx.refuse('not_found', 'no such content reference');
  end if;
  if infrx.now() >= c.expires_at then
    perform infrx.refuse('result_expired', 'the content reference has expired');
  end if;
  select * into r from infrx.lab_content_rights(c.grantor_org_id, c.provider_org_id,
                                                c.user_id, c.request_id, c.purpose,
                                                c.categories);
  if not found then
    perform infrx.refuse('forbidden', 'no current access grant for this provider, request, '
                         'category and purpose');
  end if;
  if infrx.now() >= r.retained_until then
    perform infrx.refuse('result_expired', 'the content is past its grant''s retention');
  end if;
  return infrx.lab_content_ref_json(c, least(c.expires_at, r.retained_until),
                                    r.grant_version);
end $$;

create or replace function infrx.lab_content_ref_held(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('held', exists (
    select 1 from infrx.lab_content_refs c
      cross join lateral infrx.lab_content_rights(c.grantor_org_id, c.provider_org_id,
                                                  c.user_id, c.request_id, c.purpose,
                                                  c.categories) r
     where c.grantor_org_id::text = p_args->>'org_id'
       and c.request_id::text = p_args->>'request_id'
       and infrx.now() < c.expires_at and infrx.now() < r.retained_until))
$$;

-- ================================================================ tombstones ===
create or replace function infrx.lab_tombstone_samples(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_ids uuid[];
  v_new uuid[];
begin
  begin
    v_ids := array(select jsonb_array_elements_text(p_args->'sample_ids')::uuid);
    with stoned as (
      insert into infrx.lab_sample_tombstones (provider_org_id, sample_id, reason)
      select (p_args->>'provider_org_id')::uuid, s, p_args->>'reason'
        from unnest(v_ids) s
      on conflict (provider_org_id, sample_id) do nothing
      returning sample_id)
    select coalesce(array_agg(sample_id order by sample_id), '{}') into v_new from stoned;
  exception when invalid_text_representation or invalid_parameter_value
                 or not_null_violation or check_violation or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a tombstone names the provider, sample ids and '
                         'a reason word');
  end;
  return jsonb_build_object('tombstoned', to_jsonb(v_new));
end $$;

create or replace function infrx.lab_bound_samples(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid;
  n int;
begin
  begin
    v_provider := (p_args->>'provider_org_id')::uuid;
    insert into infrx.lab_sample_bounds (provider_org_id, sample_id, content_until)
    select v_provider, (b->>'sample_id')::uuid, (b->>'content_until')::timestamptz
      from jsonb_array_elements(p_args->'bounds') b
    on conflict (provider_org_id, sample_id) do nothing;
  exception when invalid_text_representation or invalid_parameter_value
                 or invalid_datetime_format or not_null_violation
                 or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a bound names a sample id and a content_until');
  end;
  select count(*) into n from jsonb_array_elements(p_args->'bounds') b
    join infrx.lab_sample_bounds s on s.provider_org_id = v_provider
     and s.sample_id = (b->>'sample_id')::uuid
   where s.content_until <> (b->>'content_until')::timestamptz;
  if n > 0 then
    perform infrx.refuse('idempotency_conflict', 'a sample already has another content bound');
  end if;
  return jsonb_build_object('bounded', jsonb_array_length(p_args->'bounds'));
end $$;

create or replace function infrx.lab_blocked_samples(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_object_agg(s.sample_id,
           coalesce(t.reason, 'content_expired') order by s.sample_id), '{}')
    from infrx.lab_records r
    join infrx.lab_dataset_samples s on s.dataset_ref = r.ref
    left join infrx.lab_sample_tombstones t
      on t.provider_org_id = r.provider_org_id and t.sample_id = s.sample_id
    left join infrx.lab_sample_bounds b
      on b.provider_org_id = r.provider_org_id and b.sample_id = s.sample_id
   where r.ref = p_args->>'dataset_ref'
     and r.provider_org_id::text = p_args->>'provider_org_id'
     and (t.sample_id is not null or infrx.now() >= b.content_until)
$$;

create or replace function infrx.lab_permitted_samples(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(x order by x), '[]')
    from (select infrx.lab_blocked_samples(p_args) blocked) b,
         jsonb_array_elements_text(infrx.lab_accessible_samples(p_args)) x
   where not b.blocked ? x
$$;

-- ============================================================== session door ===
create or replace function public.lab_content_ref_issue(p_handle_sha256 text,
  p_provider_org_id uuid, p_grant_ref text, p_request_id uuid, p_purpose text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.require_feature('lab_content');
  return infrx.lab_content_ref_issue(jsonb_build_object(
    'handle_sha256', p_handle_sha256, 'user_id', auth.uid(),
    'provider_org_id', p_provider_org_id, 'grant_ref', p_grant_ref,
    'request_id', p_request_id, 'purpose', p_purpose,
    'categories', jsonb_build_array('request_content', 'response_content'), 'ttl_s', 300));
end $$;

revoke all on function public.lab_content_ref_issue(text, uuid, text, uuid, text)
  from public, anon;
grant execute on function public.lab_content_ref_issue(text, uuid, text, uuid, text)
  to authenticated;
