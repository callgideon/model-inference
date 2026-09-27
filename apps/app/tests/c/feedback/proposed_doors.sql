-- C3F (wave-5 LW2) WR-C3F-1: the two session doors C3F's adapters call. PROPOSED for lab-sql (one SQL
-- writer): its migration, numbered at merge, LOCAL-ONLY (R151). Until then stack.py applies this file
-- to the task-local database only, and only when no migration defines the doors. Strictly additive:
-- no table, row, flag or existing grant changes. PostgREST exposes `public` only (never `infrx`).
--
--   public.submit_feedback(p_args)      {request_id, name, value, comment?, idempotency_key}: the
--       signed-in individual's feedback on a request of an org they are a MEMBER of (else
--       `not_found`, the answer an unknown id gets). Everything but the signal is the server's:
--       org = the job's, author = auth.uid(), channel `console`, role `customer` (0028),
--       by_operator = is_operator() (R41's marker), operation `feedback.submit`, and a digest of the
--       canonical signal; `infrx.accept_feedback` owns flag, suspension, replay and conflict.
--   public.lab_review_feedback(p_args)  {provider_org_id, request_id}: a provider member's review of
--       the customer signals on a request shared with that provider. L2's rule on the database clock
--       (as `LabAccess.authorize_content`): a CURRENT membership of the provider (else `not_found`,
--       as `_member`), holding manage_dev_deployment (developer, administrator; else `forbidden`),
--       AND the request's org's CURRENT grant version to the provider naming the job's model, the
--       `feedback` category and the `provider_sharing` purpose - else `not_found`, so another org's
--       request, a revoked, expired or narrower grant and an unknown id are one answer. Labels are
--       never returned (R49); nor are the customer's principal, org or operator marker.
--
-- EXECUTE: authenticated only. 0004's default privileges already withhold it from PUBLIC and anon
-- (measured: an explicit revoke changes nothing, so none is written) and give it to the platform
-- role, for which auth.uid() is null, so both doors answer `not_found`.
--
-- ROLLBACK (this file alone; nothing references it):
--   drop function public.submit_feedback(jsonb), public.lab_review_feedback(jsonb);

create or replace function public.submit_feedback(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_job uuid;
  v_org uuid;
  v_body jsonb;
begin
  if jsonb_typeof(p_args) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_args) k
                where k not in ('request_id', 'name', 'value', 'comment', 'idempotency_key'))
     or jsonb_typeof(p_args->'idempotency_key') is distinct from 'string'
     or length(p_args->>'idempotency_key') not between 1 and 200 then
    perform infrx.refuse('invalid_request', 'feedback is {request_id, name, value, comment?, '
                         'idempotency_key}; provenance is the server''s');
  end if;
  if p_args->>'request_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    v_job := (p_args->>'request_id')::uuid;
  end if;
  select j.org_id into v_org from infrx.jobs j
   where j.request_id = v_job and public.is_org_member(j.org_id);
  if v_org is null then
    perform infrx.refuse('not_found', 'no such request for this account');
  end if;
  v_body := jsonb_strip_nulls(jsonb_build_object('name', p_args->'name',
              'value', p_args->'value', 'comment', p_args->'comment'));
  return infrx.accept_feedback(jsonb_build_object(
    'org_id', v_org, 'principal', auth.uid()::text, 'by_operator', public.is_operator(),
    'channel', 'console', 'request_id', v_job,
    'feedback_id', 'fb_' || replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''),
    'body', v_body,
    'idem', jsonb_build_object('org_id', v_org, 'operation', 'feedback.submit',
      'key', p_args->>'idempotency_key',
      'payload_hash', 'sha256:' || encode(sha256(convert_to(
         (v_body || jsonb_build_object('request_id', v_job))::text, 'UTF8')), 'hex'))));
end $$;

create or replace function public.lab_review_feedback(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
declare
  v_uuid constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_provider uuid;
  v_job uuid;
  v_org uuid;
begin
  if jsonb_typeof(p_args) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_args) k
                where k not in ('provider_org_id', 'request_id')) then
    perform infrx.refuse('invalid_request', 'a review is {provider_org_id, request_id}');
  end if;
  if p_args->>'provider_org_id' ~* v_uuid then
    v_provider := (p_args->>'provider_org_id')::uuid;
  end if;
  if p_args->>'request_id' ~* v_uuid then
    v_job := (p_args->>'request_id')::uuid;
  end if;
  if not exists (select 1 from infrx.provider_memberships m
                  where m.provider_org_id = v_provider and m.user_id = auth.uid()
                    and m.granted_at <= infrx.now()
                    and (m.revoked_at is null or infrx.now() < m.revoked_at)) then
    perform infrx.refuse('not_found', 'no such provider workspace');
  end if;
  if not exists (select 1 from infrx.provider_memberships m
                  where m.provider_org_id = v_provider and m.user_id = auth.uid()
                    and m.granted_at <= infrx.now()
                    and (m.revoked_at is null or infrx.now() < m.revoked_at)
                    and m.role in ('developer', 'administrator')) then
    perform infrx.refuse('forbidden', 'this provider role does not hold manage_dev_deployment');
  end if;
  select j.org_id into v_org from infrx.jobs j
   where j.request_id = v_job
     and (select g.effective_at <= infrx.now()
                 and (g.revoked_at is null or infrx.now() < g.revoked_at)
                 and (g.expires_at is null or infrx.now() < g.expires_at)
                 and j.model_id::text = any(g.model_ids)
                 and 'feedback' = any(g.categories) and 'provider_sharing' = any(g.purposes)
            from infrx.lab_access_grants g
           where g.grantor_org_id = j.org_id and g.recipient_provider_org_id = v_provider
           order by g.version desc limit 1);
  if v_org is null then
    perform infrx.refuse('not_found', 'no such request shared with this provider');
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('feedback_id', f->'feedback_id',
            'request_id', f->'request_id', 'name', f->'name', 'value', f->'value',
            'comment', f->'comment', 'author_role', f->'author_role', 'channel', f->'channel',
            'created_at', f->'created_at') order by n), '[]')
    from jsonb_array_elements(infrx.request_feedback(jsonb_build_object(
           'request_id', v_job, 'org_id', v_org))) with ordinality as r(f, n));
end $$;

grant execute on function public.submit_feedback(jsonb) to authenticated;
grant execute on function public.lab_review_feedback(jsonb) to authenticated;
