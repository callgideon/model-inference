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
--   lab_checkpoint_receipt {provider_org_id, checkpoint_id}: WR-C5-RECEIPT - the D7 receipt
--       of a checkpoint the provider received (0029/0034's `lab_checkpoint_receipts`):
--       `{external_run_ref, artifact_digest, state}`, or null for another provider's or an
--       unknown id (B3's suites answer `not_found` for it). Replaces the checkpoint
--       composition's own select.
--   lab_providers_with {work: judge | release, states: [...]}: WR-C5-PROVIDERS - the
--       providers (sorted, distinct) with a D6J judge run (`lab_judge_runs`) or a D9 release
--       (`lab_rollouts`) in one of the given states: the Lab worker passes' provider list
--       (judge: ambiguous/submitted; rollout: running/rolled_back). `states` is required and
--       non-empty, `work` one of the two (never an unbounded scan). Replaces the judge
--       role's select of every provider org and the rollout role's listing of objects.
--
-- RPCs: SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults (the same
-- surface as 0048/0049's listings).
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_release_decisions(jsonb), infrx.lab_checkpoint_receipt(jsonb),
--   infrx.lab_providers_with(jsonb).
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

create or replace function infrx.lab_checkpoint_receipt(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('external_run_ref', r.external_run_ref,
                            'artifact_digest', r.artifact_digest, 'state', r.state)
    from infrx.lab_checkpoint_receipts r
   where r.provider_org_id = (p_args->>'provider_org_id')::uuid
     and r.checkpoint_id = (p_args->>'checkpoint_id')::uuid
$$;

create or replace function infrx.lab_providers_with(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_states text[] := array(select jsonb_array_elements_text(
                             coalesce(p_args->'states', '[]'::jsonb)));
  v_found text[];
begin
  if cardinality(v_states) = 0 then
    perform infrx.refuse('invalid_request', 'a provider listing names at least one state');
  end if;
  if p_args->>'work' = 'judge' then
    v_found := array(select distinct r.provider_org_id::text from infrx.lab_judge_runs r
                      where r.state = any(v_states));
  elsif p_args->>'work' = 'release' then
    v_found := array(select distinct o.provider_org_id::text from infrx.lab_rollouts o
                      where o.state = any(v_states));
  else
    perform infrx.refuse('invalid_request', 'a provider listing is of judge or release work');
  end if;
  return (select coalesce(jsonb_agg(p order by p), '[]') from unnest(v_found) p);
end $$;
