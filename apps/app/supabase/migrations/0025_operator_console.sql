-- D10-0025: the operator console's audited writes and two operator-only reads (U3 WR-U3-1,
-- R143). U3's proposal (apps/app/tests/u/operator_rpc_proposed.sql) as a migration: the RPC
-- names, arguments, answers and error codes are the proposal's, which the App calls. No
-- table, row, money path or runtime grant changes; nothing becomes executable by anon, and
-- service_role gains no EXECUTE.
--
--   infrx.console_operator            the shared guard: `public.is_operator()` for the
--     (p_reason, p_idempotency_key)   JWT's subject, else 42501 `forbidden: …`; a reason
--                                     of 1..500 and a key of 1..200 characters, else
--                                     `invalid_request`; answers the audit actor
--                                     `operator:<auth.uid()>` - never a parameter.
--   public.operator_adjust_credit     0018 `grant_credit` as an `operator_adjustment` on
--     (p_user, p_amount, p_reason,    the individual's consumer wallet: signed exact text,
--      p_idempotency_key)             nonzero, never below the reserved total
--                                     (`invalid_request`), `not_found` without a wallet.
--                                     Operation id md5('app-operator:adjust:' || key)::uuid:
--                                     a replay answers `replayed`, another wallet or amount
--                                     is `idempotency_conflict` (0018's rules, also when
--                                     concurrent). Answers {replayed, unit, amount}.
--   public.operator_set_suspension    0009 `set_suspension`, closed code `other`, the
--     (p_org, p_suspended, p_reason,  operator's prose as the audit reason (R59 (2)); audit
--      p_idempotency_key)             key 'app-operator:suspension:' || key. The key's
--                                     recorded change answers `replayed`, another org or
--                                     status `idempotency_conflict`; `not_found` for an
--                                     unknown org. Answers {replayed, suspended}.
--   public.operator_revoke_key        0009 `revoke_key` on a CONSUMER key only (else
--     (p_key, p_reason,               `not_found`); audit key 'app-operator:revoke:' || key;
--      p_idempotency_key)             another key id under it is `idempotency_conflict`; an
--                                     already revoked key answers `replayed`, nothing
--                                     changes twice. Answers {replayed}.
--   public.operator_wallet_drift      0006's detector's drifting rows, drift as text.
--   public.operator_unknown_usage     `held_unknown` jobs (both regimes, each hold in its
--                                     own unit, as text): the 24 h queue. A CREDIT job with
--                                     unknown usage has no usage row (0018 settle_credit).
--
-- Console keys are namespaced `app-operator:<operation>:<key>`, apart from the CLI's (raw
-- key text; `stable_id(operation, key)` operation ids). A concurrent retry is a replay: the
-- adjustment serializes on 0018's wallet lock, the suspension and revocation on a
-- transaction advisory lock of their namespaced key, taken before the key is looked up.
-- Rate publication, the signup grant and the unknown-usage release stay CLI-only.
--
-- ORDER. After 0024 (0001-0024 unchanged). Apply before the App build that calls these
-- RPCs; until then the App answers "not confirmed" and the two sections "unavailable".
-- PostgREST reloads its schema cache on the DDL notification Supabase sends.
--
-- ROLLBACK (0025 alone; no data moves - ledger and audit rows written stay, append-only):
--   drop view if exists public.operator_unknown_usage;
--   drop view if exists public.operator_wallet_drift;
--   drop function if exists public.operator_revoke_key(uuid, text, text);
--   drop function if exists public.operator_set_suspension(uuid, boolean, text, text);
--   drop function if exists public.operator_adjust_credit(uuid, text, text, text);
--   drop function if exists infrx.console_operator(text, text);
-- Roll back the App build that calls them first.
--
-- Re-runnable: `create or replace`, and the grants restated.

-- ============================================================ the operator guard ===
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

-- =================================================================== adjustment ===
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

-- =================================================================== suspension ===
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
  -- Same key, one at a time (any target): the lookup below then sees what a racing call
  -- under this key committed. 0009's own replay check answers ANY org's state under a
  -- used key, so it must never be what decides a console replay.
  perform pg_advisory_xact_lock(hashtextextended(v_key, 0));
  select * into a from infrx.audit_entries where idempotency_key = v_key;
  if not found then
    -- The closed code is `other`; the operator's prose is the audit reason (R59 (2)).
    return jsonb_build_object('replayed', false, 'suspended',
      infrx.set_suspension(p_org, p_suspended, case when p_suspended then 'other' end, v_actor,
                           btrim(p_reason), v_key));
  end if;
  if a.target_org_id is distinct from p_org
     or (a.after->>'suspended')::boolean is distinct from p_suspended then
    perform infrx.refuse('idempotency_conflict', 'this key recorded another suspension change');
  end if;
  return jsonb_build_object('replayed', true, 'suspended', p_suspended);
end $$;

-- =================================================================== revocation ===
create or replace function public.operator_revoke_key(p_key uuid, p_reason text,
  p_idempotency_key text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_actor text := infrx.console_operator(p_reason, p_idempotency_key);
  v_key text := 'app-operator:revoke:' || p_idempotency_key;
  a infrx.audit_entries%rowtype;
  k public.api_keys%rowtype;
begin
  -- Same key, one at a time (any target), as the suspension: a key reused for another
  -- credential while its first use commits is a conflict, never a raw unique violation.
  perform pg_advisory_xact_lock(hashtextextended(v_key, 0));
  select * into a from infrx.audit_entries where idempotency_key = v_key;
  if found then
    if a.after->>'key_id' is distinct from p_key::text then
      perform infrx.refuse('idempotency_conflict', 'this key recorded another revocation');
    end if;
    return jsonb_build_object('replayed', true);
  end if;
  -- Consumer keys only; the row lock orders a retry under another key behind this one.
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

-- ========================================================== operator-only reads ===
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

-- ================================================================ privileges ===
-- R59-4: Supabase's default ACL hands anon/authenticated/service_role EXECUTE on a new
-- public function and ALL on a new public view; revoke that, then grant exactly: the three
-- RPCs to `authenticated` (is_operator() inside decides), the guard to nobody (only these
-- definer bodies call it), the views SELECT-only. service_role keeps no write on the views:
-- `operator_wallet_drift` is a simple view over 0006's detector over `credit_wallets`,
-- whose writes 0006 took from the platform role.
revoke all on function infrx.console_operator(text, text)
  from public, anon, authenticated, service_role;
revoke all on function public.operator_adjust_credit(uuid, text, text, text)
  from public, anon, authenticated, service_role;
revoke all on function public.operator_set_suspension(uuid, boolean, text, text)
  from public, anon, authenticated, service_role;
revoke all on function public.operator_revoke_key(uuid, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.operator_adjust_credit(uuid, text, text, text) to authenticated;
grant execute on function public.operator_set_suspension(uuid, boolean, text, text)
  to authenticated;
grant execute on function public.operator_revoke_key(uuid, text, text) to authenticated;
revoke all on public.operator_wallet_drift, public.operator_unknown_usage
  from public, anon, authenticated, service_role;
grant select on public.operator_wallet_drift, public.operator_unknown_usage
  to authenticated, service_role;
