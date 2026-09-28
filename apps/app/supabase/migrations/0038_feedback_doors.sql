-- WR-C3F-1 (wave-5 LW2 request from the feedback lane's C3F; lane lab-sql, D6F): the two
-- session doors C3F's adapters call, EXACTLY as proposed in
-- `apps/app/tests/c/feedback/proposed_doors.sql` (the tip; below from its first `create`).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge. Once this
-- migration is applied, C3F's stack.py applies nothing (it only fills the doors when no
-- migration defines them). Strictly additive: no table, row, flag or existing grant changes.
--
--   public.submit_feedback(p_args)      {request_id, name, value, comment?, idempotency_key}:
--       the signed-in individual's feedback on a request of an org they are a MEMBER of (else
--       `not_found`). Provenance is the server's (org = the job's, author = auth.uid(),
--       channel `console`, role `customer`, R41's operator marker, operation
--       `feedback.submit`, a digest of the canonical signal); `infrx.accept_feedback` owns the
--       flag (D6F's `feedback`, OFF), suspension, replay and conflict.
--   public.lab_review_feedback(p_args)  {provider_org_id, request_id}: a CURRENT developer+
--       member's read of the customer signals on a request whose org's CURRENT grant to the
--       provider names the job's model, `feedback` and `provider_sharing` (else `not_found`;
--       a viewer is `forbidden`). No label, principal, org or operator marker leaves.
--
-- EXECUTE: authenticated only (0004's default privileges withhold it from PUBLIC and anon and
-- give it to the platform role, for which auth.uid() is null, so both doors answer
-- `not_found`).
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
