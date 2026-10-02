-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0068 is allocated to api-schema-3 (AP-00,
-- wave 7 batch 3; register rows 91 and 94). The batch's late schema requests and the E4C
-- run-2 database findings, in one file:
--
--   SR-AP06-1 (api-publication, evidence/w7/api-publication-5be6dbb.md): the control login's
--     dev-credential reads and revocation (SECURITY DEFINER, 0004's service_role default +
--     EXECUTE to infrx_lab_control only):
--     infrx.lab_control_dev_keys {provider_org_id, endpoint_id} -> [{key_id, endpoint_id, name,
--       prefix, created_at, revoked_at}]: the keys of the provider's own DEV endpoint, oldest
--       first (never the hash; 0009's check makes every key naming a provider and endpoint a
--       provider_dev key); another provider's or a prod endpoint is `not_found` (0032's
--       lab_control_dev_key refusal).
--     infrx.lab_control_revoke_dev_key {provider_org_id, endpoint_id, key_id, actor,
--       idempotency_key} -> the key as listed: one-way through 0009's revoke_key (its
--       admin_key_revoke audit entry) and a `lab_dev_key_revoke` control event, both once; a
--       revoked key answers its first revoked_at again; a key outside that endpoint is
--       `not_found`. The audit entry's idempotency key is scoped to the key
--       (`lab_dev_key_revoke:<key id>:<key>`): 0009's unique index is global, and two tenants
--       may send the same Idempotency-Key. 0032's action check admits `lab_dev_key_revoke`.
--     infrx.lab_control_dev_wallet {provider_org_id} -> {opened, balance}: the provider_dev
--       wallet's available CREDIT (ledger - reserved) as exact text (`0.00000000` and
--       opened=false before the first allocation; the request said numeric - text keeps it
--       exact across JSON, as job_admission's money does).
--
-- ROLLBACK (this file alone; nothing earlier references it; tests/d/test_control_ops_upgrade.py runs these lines):
-- rollback: drop function if exists infrx.lab_control_dev_keys(jsonb), infrx.lab_control_revoke_dev_key(jsonb), infrx.lab_control_dev_wallet(jsonb);
-- rollback: alter table infrx.lab_control_events drop constraint if exists lab_control_events_action_check, add constraint lab_control_events_action_check check (action in ('lab_transition', 'lab_propose', 'lab_dev_key', 'lab_publish', 'lab_rollback', 'lab_fund'));
-- (the restored check refuses while a `lab_dev_key_revoke` event exists: control events are
-- history, never deleted - roll back only before the first revocation)

-- ================================================================ SR-AP06-1 ===
alter table infrx.lab_control_events drop constraint if exists lab_control_events_action_check,
  add constraint lab_control_events_action_check check (action in ('lab_transition',
    'lab_propose', 'lab_dev_key', 'lab_publish', 'lab_rollback', 'lab_fund', 'lab_dev_key_revoke'));

create or replace function infrx.lab_control_dev_keys(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if not exists (select 1 from infrx.endpoints where endpoint_id = (p_args->>'endpoint_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid and environment = 'dev') then
    perform infrx.refuse('not_found', 'no such dev endpoint for this provider');
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('key_id', k.id,
      'endpoint_id', k.endpoint_id, 'name', k.name, 'prefix', k.prefix, 'created_at',
      k.created_at, 'revoked_at', k.revoked_at) order by k.created_at, k.id), '[]')
    from public.api_keys k
   where k.provider_org_id = (p_args->>'provider_org_id')::uuid
     and k.endpoint_id = (p_args->>'endpoint_id')::uuid);
end $$;

create or replace function infrx.lab_control_revoke_dev_key(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  k public.api_keys%rowtype;
  v_at timestamptz;
begin
  select * into k from public.api_keys a where a.id = (p_args->>'key_id')::uuid
     and a.provider_org_id = (p_args->>'provider_org_id')::uuid
     and a.endpoint_id = (p_args->>'endpoint_id')::uuid
   for update;
  if not found then
    perform infrx.refuse('not_found', 'no such dev credential on this endpoint');
  end if;
  v_at := infrx.revoke_key(k.id, p_args->>'actor', 'lab_dev_key_revoke',
    'lab_dev_key_revoke:' || k.id || ':' || (p_args->>'idempotency_key'));
  if k.revoked_at is null then
    perform infrx.lab_control_audit(k.provider_org_id, 'lab_dev_key_revoke', p_args->>'actor',
      k.id::text, jsonb_build_object('endpoint_id', k.endpoint_id, 'revoked_at', v_at));
  end if;
  return jsonb_build_object('key_id', k.id, 'endpoint_id', k.endpoint_id, 'name', k.name,
    'prefix', k.prefix, 'created_at', k.created_at, 'revoked_at', v_at);
end $$;

create or replace function infrx.lab_control_dev_wallet(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('opened', w.wallet_id is not null,
                            'balance', coalesce(w.available, 0)::numeric(20,8)::text)
  from (select 1) one
  left join infrx.credit_wallets w
    on w.kind = 'provider_dev' and w.owner_provider_org_id = (p_args->>'provider_org_id')::uuid
$$;

revoke all on function infrx.lab_control_dev_keys(jsonb), infrx.lab_control_revoke_dev_key(jsonb),
  infrx.lab_control_dev_wallet(jsonb) from public, anon, authenticated;
grant execute on function infrx.lab_control_dev_keys(jsonb), infrx.lab_control_revoke_dev_key(jsonb),
  infrx.lab_control_dev_wallet(jsonb) to infrx_lab_control;
