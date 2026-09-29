-- E3L-F4 (lane lab-control-2): an operator rejects a publication proposal. Additive: two
-- functions and their grants; no table, column, constraint or existing function changes.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- Before this file no L3 operation took a proposal out of `proposed_public` except the
-- operator's approval (`lab_control_publish`), so a proposal the operator declined stayed open
-- forever (and R205/R214 answered every later proposal of its dev revision with it).
--
--   lab_control_reject    {deployment_revision_id, actor, reason}: CAS proposed_public ->
--                         retired (0007's guard allows exactly that move; visibility is
--                         immutable, so the row keeps `public`: `PgControlStore` reads a
--                         retired revision back as private - no DeploymentRevision is public
--                         and retired). Absent -> not_found, any other state ->
--                         state_conflict (a listed or already decided revision is never
--                         rejected), a blank or over-long reason -> invalid_request. Audited
--                         as `lab_transition` {state: proposed_public} -> {state: retired,
--                         reason} under the operator. Listings are not touched; the dev source
--                         stays `ready_private`, so the provider may propose it again.
--   lab_control_operator  {user_id}: {operator: profiles.is_operator} of that user (false for
--                         an unknown one) - the control service's check that a Lab session
--                         is a platform operator's (the actor is then `operator:<user_id>`,
--                         0025's `console_operator` naming).
--
-- Doors (0025's pattern): nothing for public/anon/authenticated; EXECUTE for service_role
-- (the gateway's composition) and `infrx_lab_control` (the control factory's own login,
-- 0043 WR-I2L-4), which already holds publish/rollback.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_control_reject(jsonb), infrx.lab_control_operator(jsonb). Rejected revisions
--   and their audit rows stay (history).
--
-- Re-runnable: `create or replace`, and the grants restated.

create or replace function infrx.lab_control_reject(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  d infrx.deployment_revisions%rowtype;
begin
  select * into d from infrx.deployment_revisions
   where deployment_revision_id = (p_args->>'deployment_revision_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such proposal');
  end if;
  if d.state <> 'proposed_public' then
    perform infrx.refuse('state_conflict', 'only an open proposal is rejected; this revision '
                         'is ' || d.state);
  end if;
  if length(btrim(coalesce(p_args->>'reason', ''))) not between 1 and 500 then
    perform infrx.refuse('invalid_request', 'a rejection states its reason (1-500 characters)');
  end if;
  update infrx.deployment_revisions set state = 'retired'
   where deployment_revision_id = d.deployment_revision_id returning * into d;
  perform infrx.lab_control_audit(d.provider_org_id, 'lab_transition', p_args->>'actor',
    d.deployment_revision_id::text,
    jsonb_build_object('state', d.state, 'reason', p_args->>'reason'),
    jsonb_build_object('state', 'proposed_public'));
  return to_jsonb(d);
end $$;

create or replace function infrx.lab_control_operator(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('operator', coalesce((select p.is_operator from public.profiles p
     where p.id = (p_args->>'user_id')::uuid), false))
$$;

revoke all on function infrx.lab_control_reject(jsonb), infrx.lab_control_operator(jsonb)
  from public, anon, authenticated;
grant execute on function infrx.lab_control_reject(jsonb), infrx.lab_control_operator(jsonb)
  to service_role, infrx_lab_control;
