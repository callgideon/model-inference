-- U3 WIRING REQUEST WR-U3-1 (for D10) - PROPOSED, NOT A MIGRATION.
--
-- U3 does not write migrations (only D10 does). This file is the exact SQL U3 asks D10 to ship
-- as its next migration; tests/u/operator_stack.py applies it ONLY to the task-local database, on
-- top of every committed migration, to prove the composed App path (tests/u/operator-postgrest.test.ts).
--
-- The operator console's audited writes, reachable by the signed-in OPERATOR's own JWT through
-- PostgREST (which exposes `public` only) - no service key in the App, no new schema exposed:
--
--   * each function checks `public.is_operator()` IN THE DATABASE, so a consumer, provider or
--     anonymous session that crafts the RPC is refused (42501) whatever the route did;
--   * the audit actor is `operator:<auth.uid()>`, derived here, never a parameter;
--   * each one runs the SAME audited, idempotent `infrx` operation the operations CLI uses
--     (0018 `grant_credit`, 0009 `set_suspension`, 0009 `revoke_key`) - no second grant, hold or
--     settlement SQL;
--   * the console's idempotency keys live in their own namespace (`app-operator:<op>:<key>`), so
--     they can never collide with a CLI key; a key reused for a different change is
--     `idempotency_conflict`, a concurrent retry is a replay (never a raw unique violation);
--   * an adjustment is `grant_credit`'s `operator_adjustment` (signed, nonzero, exact text, never
--     below the reserved total), so the console can correct or add credit but never set a balance.
--
-- Plus two operator-only reads: `public.operator_wallet_drift`, the 0006 reconciliation detector's
-- drifting rows (the detector itself is service_role only), and `public.operator_unknown_usage`,
-- the 24 h reconciliation queue (a CREDIT job with unknown usage has no `usage_events` row -
-- 0018 `settle_credit` - so `console_usage` cannot show it).

create or replace function infrx.console_operator(p_reason text, p_idempotency_key text)
returns text language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if auth.uid() is null or not public.is_operator() then
    raise exception 'forbidden: operator authority is required' using errcode = '42501';
  end if;
  if length(btrim(coalesce(p_reason, ''))) not between 1 and 500
     or length(coalesce(p_idempotency_key, '')) not between 1 and 200 then
    perform infrx.refuse('invalid_request', 'an operator change needs a reason (1..500) and an '
                         || 'idempotency key (1..200)');
  end if;
  return 'operator:' || auth.uid()::text;
end $$;

create or replace function public.operator_adjust_credit(p_user uuid, p_amount text,
  p_reason text, p_idempotency_key text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_actor text := infrx.console_operator(p_reason, p_idempotency_key);
  v_wallet uuid;
  v_answer jsonb;
begin
  select w.wallet_id into v_wallet from infrx.credit_wallets w
   where w.owner_user_id = p_user and w.kind = 'consumer';
  if v_wallet is null then
    perform infrx.refuse('not_found', 'no consumer CREDIT wallet for that individual');
  end if;
  v_answer := infrx.grant_credit(jsonb_build_object(
    'wallet_id', v_wallet, 'kind', 'operator_adjustment', 'amount', p_amount,
    'operation_id', md5('app-operator:adjust:' || p_idempotency_key)::uuid,
    'actor', v_actor, 'reason', btrim(p_reason), 'at', now()));
  return jsonb_build_object('replayed', (v_answer->>'replayed')::boolean, 'unit', 'CREDIT',
                            'amount', v_answer->'entry'->>'amount');
end $$;

create or replace function public.operator_set_suspension(p_org uuid, p_suspended boolean,
  p_reason text, p_idempotency_key text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_actor text := infrx.console_operator(p_reason, p_idempotency_key);
  v_key text := 'app-operator:suspension:' || p_idempotency_key;
  a infrx.audit_entries%rowtype;
begin
  if p_org is null or p_suspended is null then
    perform infrx.refuse('invalid_request', 'an organization and a status are required');
  end if;
  select * into a from infrx.audit_entries where idempotency_key = v_key;
  if not found then
    begin
      -- The closed code is `other`; the operator's prose is the audit reason (R59 (2)).
      return jsonb_build_object('replayed', false, 'suspended',
        infrx.set_suspension(p_org, p_suspended, case when p_suspended then 'other' end, v_actor,
                             btrim(p_reason), v_key));
    exception when unique_violation then
      -- A concurrent call under the same key committed first: answer it below.
      select * into a from infrx.audit_entries where idempotency_key = v_key;
    end;
  end if;
  if a.target_org_id is distinct from p_org
     or (a.after->>'suspended')::boolean is distinct from p_suspended then
    perform infrx.refuse('idempotency_conflict', 'this key recorded another suspension change');
  end if;
  return jsonb_build_object('replayed', true, 'suspended', p_suspended);
end $$;

create or replace function public.operator_revoke_key(p_key uuid, p_reason text,
  p_idempotency_key text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_actor text := infrx.console_operator(p_reason, p_idempotency_key);
  v_key text := 'app-operator:revoke:' || p_idempotency_key;
  a infrx.audit_entries%rowtype;
  k public.api_keys%rowtype;
begin
  select * into a from infrx.audit_entries where idempotency_key = v_key;
  if found then
    if a.after->>'key_id' is distinct from p_key::text then
      perform infrx.refuse('idempotency_conflict', 'this key recorded another revocation');
    end if;
    return jsonb_build_object('replayed', true);
  end if;
  -- Consumer keys only; the row lock orders a concurrent retry behind this one.
  select * into k from public.api_keys where id = p_key and audience = 'consumer' for update;
  if not found then
    perform infrx.refuse('not_found', 'no such consumer key');
  end if;
  if k.revoked_at is not null then
    return jsonb_build_object('replayed', true);        -- already revoked: nothing changes twice
  end if;
  perform infrx.revoke_key(p_key, v_actor, btrim(p_reason), v_key);
  return jsonb_build_object('replayed', false);
end $$;

-- ponytail: scans every wallet per read (the 0006 detector); fine at pilot volume, index or
-- materialize it when wallets reach the hundreds of thousands.
create or replace view public.operator_wallet_drift with (security_barrier = true) as
select r.wallet_id, r.kind, r.ledger_drift::text as ledger_drift,
       r.reserved_drift::text as reserved_drift
from infrx.credit_wallet_reconciliation r
where (r.ledger_drift <> 0 or r.reserved_drift <> 0)
  and (public.is_operator() or public.is_service_client());

-- Both regimes, each in its own unit; the jobs partial index on `held_unknown` (0003) serves it.
create or replace view public.operator_unknown_usage with (security_barrier = true) as
select j.request_id, j.org_id, j.created_at, j.reconcile_after,
       case j.accounting_regime when 'credit' then 'CREDIT' else 'USD' end as unit,
       coalesce(ch.amount, uh.amount)::text as hold
from infrx.jobs j
left join infrx.credit_wallet_holds ch
  on ch.request_id = j.request_id and ch.state in ('held', 'unknown')
left join infrx.credit_holds uh on uh.request_id = j.request_id and uh.state in ('held', 'unknown')
where j.settlement_state = 'held_unknown'
  and (public.is_operator() or public.is_service_client());

revoke all on function infrx.console_operator(text, text)
  from public, anon, authenticated, service_role;
do $$
declare
  f text;
begin
  foreach f in array array['public.operator_adjust_credit(uuid,text,text,text)',
                           'public.operator_set_suspension(uuid,boolean,text,text)',
                           'public.operator_revoke_key(uuid,text,text)'] loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role', f);
    execute format('grant execute on function %s to authenticated', f);
  end loop;
end $$;
revoke all on public.operator_wallet_drift, public.operator_unknown_usage
  from public, anon, authenticated;
grant select on public.operator_wallet_drift, public.operator_unknown_usage
  to authenticated, service_role;
