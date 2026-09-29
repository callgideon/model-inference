-- Composition batch 6 (lane composition-6): the reads the composed Lab surfaces and worker
-- roles still did with a select of their own, or could not do at all. Additive: functions
-- only; no table, column, constraint, grant change or existing function is touched.
-- LOCAL-ONLY (R150/R151/R201): never applied hosted; the number is the next free at merge.
--
--   lab_release_decisions {provider_org_id}: WR-R4-2's records port (`/lab/v1/releases`'
--       `decisions`) - every D9 decision (`lab_rollout_events` rows that carry one:
--       expand / hold / rollback) of the provider's releases, oldest first, each
--       `{policy_ref, decision, reasons, evidence_refs, decided_by, decided_at}`. Another
--       provider's releases never appear; a provider with none gets `[]`.
--
-- RPCs: SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults (the same
-- surface as 0048/0049's listings).
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_release_decisions(jsonb).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.lab_release_decisions(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'policy_ref', e.policy_ref, 'decision', e.decision, 'reasons', to_jsonb(e.reasons),
      'evidence_refs', to_jsonb(e.evidence_refs), 'decided_by', e.decided_by,
      'decided_at', e.at) order by e.at, e.policy_id, e.fence), '[]')
    from infrx.lab_rollout_events e
    join infrx.lab_rollouts o using (policy_id)
   where o.provider_org_id = (p_args->>'provider_org_id')::uuid
     and e.decision is not null
$$;
