-- WR-E8L-4 (lane lab-sql-lw5): an operator RPC to raise `lab_rollouts.shadow_limit`. 0043
-- added the column only ("no provider-funded duplicate runs until an operator raises it");
-- until now the only way was an owner UPDATE (E8L's k02 evidence). LOCAL-ONLY (R151/R201):
-- never applied hosted; the number is the next free at merge.
--
-- `public.operator_raise_lab_shadow_limit(p_policy_id, p_shadow_limit, p_reason,
--   p_idempotency_key) -> {policy_id, shadow_limit}`: `infrx.console_operator` (0025's guard,
-- verbatim) authenticates the caller and validates reason/idempotency-key shape; raises
-- `lab_rollouts.shadow_limit` to `p_shadow_limit`, never lowers it (P-12: a public shadow
-- run is opt-in and additive, an operator does not silently shrink a bound another operator
-- already granted), and is naturally idempotent - the same call twice, or a call at or below
-- the value already in force, is a no-op that answers the row's current bound rather than a
-- write. No new audit table: monotonic-raise-only needs no idempotency ledger to stay
-- replay-safe (unlike 0025's suspension/revocation, which move state that could otherwise
-- flip-flop under a reused key).
--
-- ROLLBACK (this file alone): revoke execute on function
-- public.operator_raise_lab_shadow_limit(uuid, int, text, text) from authenticated; drop
-- function public.operator_raise_lab_shadow_limit(uuid, int, text, text).

create or replace function public.operator_raise_lab_shadow_limit(p_policy_id uuid,
  p_shadow_limit int, p_reason text, p_idempotency_key text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_actor text := infrx.console_operator(p_reason, p_idempotency_key);
  o infrx.lab_rollouts%rowtype;
begin
  if p_shadow_limit is null or p_shadow_limit < 0 then
    perform infrx.refuse('invalid_request', 'a shadow limit is a non-negative integer');
  end if;
  select * into o from infrx.lab_rollouts where policy_id = p_policy_id for update;
  if o.policy_id is null then
    perform infrx.refuse('not_found', 'no such rollout');
  end if;
  if p_shadow_limit > o.shadow_limit then
    update infrx.lab_rollouts set shadow_limit = p_shadow_limit where policy_id = p_policy_id;
    o.shadow_limit := p_shadow_limit;
  end if;
  return jsonb_build_object('policy_id', o.policy_id, 'shadow_limit', o.shadow_limit);
end $$;

revoke all on function public.operator_raise_lab_shadow_limit(uuid, int, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.operator_raise_lab_shadow_limit(uuid, int, text, text)
  to authenticated;
