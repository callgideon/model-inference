-- FAKE until 0066 merges (api-schema-2 c43a4aac, 0066_wave7_grants_and_reads.sql, AP-07 section):
-- the revoke-only door copied verbatim, installed on each ap07 case database by
-- tests/ap07/conftest.py. Delete this file and that fixture at the merge.
create or replace function infrx.lab_withdraw_access_grant(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_grantor uuid := (p_args->>'grantor_org_id')::uuid;
  prev infrx.lab_access_grants%rowtype;
  g infrx.lab_access_grants%rowtype;
begin
  -- 0027's lab_grant_version without its suspension refusal: the owner, the pair's lock
  if not exists (select 1 from public.org_members m where m.org_id = v_grantor
                 and m.user_id = (p_args->>'actor_user_id')::uuid and m.role = 'owner') then
    perform infrx.refuse('forbidden', 'only the owner of the grantor organization writes');
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'lab_access_grants/' || v_grantor || '/' || (p_args->>'recipient_provider_org_id'), 0));
  select * into prev from infrx.lab_access_grants
   where grantor_org_id = v_grantor
     and recipient_provider_org_id = (p_args->>'recipient_provider_org_id')::uuid
   order by version desc limit 1;
  if prev.grant_id is null or prev.revoked_at is not null then
    perform infrx.refuse('state_conflict', 'no current grant to withdraw');
  end if;
  insert into infrx.lab_access_grants (grant_id, version, grantor_org_id,
    recipient_provider_org_id, model_ids, categories, purposes, retention_days, effective_at,
    expires_at, revoked_at, written_by)
  values (prev.grant_id, prev.version + 1, prev.grantor_org_id, prev.recipient_provider_org_id,
    prev.model_ids, prev.categories, prev.purposes, prev.retention_days, prev.effective_at,
    prev.expires_at, infrx.now(), (p_args->>'actor_user_id')::uuid)
  returning * into g;
  return infrx.lab_grant_json(g);
end $$;
