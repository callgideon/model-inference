-- WR-R4-1 (lab-sql half, lane lab-sql-lw6; LAB-UI-SWAP, the release read-model request) and
-- WR-R2-3's release half (13-lab-improvement-handoffs; the rollout pass loop's D9 input):
-- a LISTING of a provider's releases, each with R2's latest verdict.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- 0039's `lab_release` reads exactly ONE release, by its policy_ref (the caller must
-- already know it). Neither `/lab/v1/releases` (R4's page: every release of a provider) nor
-- the rollout pass loop (R2's `Controller.step` over "every running/rolled-back D9 release")
-- can be served by that alone. This adds a LISTING RPC, over 0033/0039's rows only (D9's own
-- tables; no new table, no new column):
--
--   lab_releases_in {provider_org_id, states?: [...]}: the provider's releases (0033's
--       `lab_rollouts`), each `lab_release_json`'s fields (state, fence, plan_digest,
--       started_at) PLUS policy_id, endpoint_id, policy_ref and `latest_decision` - the most
--       recent `lab_rollout_events` row that carries a decision (`expand`/`hold`/`rollback`;
--       a release still `running` with none yet is null) - `{decision, reasons,
--       evidence_refs, decided_by, at}`. Ordered newest-started first. `states` narrows to
--       the given `lab_rollouts.state` values (R2-3: `('running', 'rolled_back')`); omitted
--       or empty, every state. A provider with no releases gets `[]`, never `not_found`.
--
-- What this migration deliberately leaves out (composition-4's job, not SQL): the full R2
-- `Plan` (thresholds/horizon/budget/protocol) is not reconstructed here - only its frozen
-- `plan_digest` (0039), the same as 0039's single-release read; the Plan itself lives
-- wherever the launcher that called `lab_release_start` kept it. R1's `Live` aggregates and
-- B2's stored report/runs are separate ports this RPC does not touch. Filed as wiring
-- requests below.
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_releases_in(jsonb), infrx.lab_release_decision_json(infrx.lab_rollout_events).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.lab_release_decision_json(e infrx.lab_rollout_events)
returns jsonb language sql immutable set search_path = infrx, public, pg_temp as $$
  select case when e.policy_id is null then null else jsonb_build_object(
    'decision', e.decision, 'reasons', to_jsonb(e.reasons),
    'evidence_refs', to_jsonb(e.evidence_refs), 'decided_by', e.decided_by, 'at', e.at) end
$$;

create or replace function infrx.lab_releases_in(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'policy_id', o.policy_id, 'provider_org_id', o.provider_org_id,
      'endpoint_id', o.endpoint_id, 'policy_ref', o.policy_ref, 'state', o.state,
      'fence', o.fence, 'plan_digest', o.plan_digest,
      'started_at', (select e0.at from infrx.lab_rollout_events e0
                      where e0.policy_id = o.policy_id and e0.action = 'start'),
      'latest_decision', infrx.lab_release_decision_json(d)) order by o.updated_at desc,
      o.policy_id), '[]')
    from infrx.lab_rollouts o
    left join lateral (
      select * from infrx.lab_rollout_events e
       where e.policy_id = o.policy_id and e.decision is not null
       order by e.fence desc limit 1) d on true
   where o.provider_org_id = (p_args->>'provider_org_id')::uuid
     and (p_args->'states' is null or jsonb_array_length(p_args->'states') = 0
          or o.state = any(array(select jsonb_array_elements_text(p_args->'states'))))
$$;
