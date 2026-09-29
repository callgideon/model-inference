-- E3L-F2 (R205, WR-lab-sql, lane lab-sql-lw6): `lab_control_propose` is idempotent - one
-- open `proposed_public` proposal per dev revision.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- Bug (research/plan/evidence/e/E3L-BIND-bab9b18.md "E3L-F2"): `LabControl.propose`
-- (infrx/lab/control/__init__.py) mints a NEW `deployment_revision_id` on every call, and
-- the old `lab_control_propose` refused only an id COLLISION (`unique_violation`). A client
-- whose answer was lost to a crash or a timeout and that retries therefore calls `propose`
-- again with a fresh, never-seen id, and the old body happily inserted a SECOND
-- `proposed_public` revision of the same dev source (l10b: two open proposals of one
-- source_revision_id, `[('edad09e2…', 'proposed_public'), ('5603688d…', 'proposed_public')]`).
--
-- Ruling (R205, proposed in `evidence/e/E3L-BIND-bab9b18.md`, numbered 2026-09-28 at the
-- e3l-bind merge on `codex/w5-merge-25`): "A publication proposal is one per dev revision
-- while it is open. A retry of a lost answer answers the open proposal (or refuses with
-- `state_conflict`), never opens a second one." This migration picks the first reading
-- (answers the open proposal), which is what `tests/integration/lab_operate/scenarios_
-- publish.py::test_l10_a_retry_after_a_lost_answer_proposes_once` needs: it does not assert
-- the retry's status code, only that the DB keeps exactly one `proposed_public` row of the
-- source - a `state_conflict` refusal would also satisfy that oracle, but "return the open
-- one" matches every other idempotent RPC in this schema (`lab_judge_reserve`,
-- `lab_judge_begin_submit`: a replay of an in-flight or already-accepted operation answers
-- the existing row, never an error), so a Lab UI retry (no idempotency key of its own,
-- because the id is minted server-side) needs no special-case error handling.
--
-- "Open" and "of this dev revision": `deployment_revisions` never records which dev source a
-- proposal came from (only `lab_control_events.after->>'source'` does, the same join
-- `tests/integration/lab_operate/scenarios_publish.py::proposals_of` uses), so identity is
-- the source_revision_id, through its most recent `lab_propose` audit row - NOT
-- (`endpoint_id`, `serving_version_id`) alone: fixture/seed data may legitimately hold
-- several unrelated `proposed_public` rows of the same serving version (e.g. two proposals
-- seeded directly to exercise a publish-CAS race), and matching on those two columns would
-- wrongly answer a fresh source's first-ever proposal with someone else's open one. Locked
-- per source (`pg_advisory_xact_lock`, the same pattern 0032's `lab_control_publish`/
-- `_rollback` use for the alias) so two concurrent proposals of the same source race safely
-- instead of both passing the pre-check and both inserting.
--
-- ROLLBACK (this file alone): `create or replace function infrx.lab_control_propose` back to
-- 0032's body (kept verbatim in that file's own history; the check/lock added below is
-- everything this migration changes).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.lab_control_propose(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  p jsonb := p_args->'proposal';
  s infrx.deployment_revisions%rowtype;
  d infrx.deployment_revisions%rowtype;
  existing infrx.deployment_revisions%rowtype;
begin
  select * into s from infrx.deployment_revisions
   where deployment_revision_id = (p_args->>'source_revision_id')::uuid for share;
  if not found or s.provider_org_id is distinct from (p->>'provider_org_id')::uuid then
    perform infrx.refuse('not_found', 'no such dev revision for this provider');
  end if;
  if (s.environment, s.visibility, s.state) <> ('dev', 'private', 'ready_private')
     or s.serving_version_id is distinct from (p->>'serving_version_id')::uuid then
    perform infrx.refuse('state_conflict', 'only a validated dev revision''s serving version '
                         'is proposed');
  end if;
  if p->>'state' is distinct from 'proposed_public' then
    perform infrx.refuse('invalid_request', 'a proposal is a proposed_public revision');
  end if;
  -- E3L-F2/R205: one open proposal per SOURCE dev revision - lock before the check so two
  -- racing proposals of the same source never both pass it and both insert.
  perform pg_advisory_xact_lock(hashtextextended('lab_control_propose/'
                                                 || (p_args->>'source_revision_id'), 0));
  select d2.* into existing from infrx.lab_control_events e
   join infrx.deployment_revisions d2 on d2.deployment_revision_id::text = e.subject
   where e.action = 'lab_propose' and e.after->>'source' = p_args->>'source_revision_id'
     and d2.state = 'proposed_public'
   order by e.at desc limit 1;
  if found then
    return to_jsonb(existing);                 -- a retry of a lost answer: the open proposal
  end if;
  begin
    insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id,
      provider_org_id, environment, serving_version_id, visibility, state, max_input_tokens,
      max_output_tokens, created_by, created_at)
    values ((p->>'deployment_revision_id')::uuid, (p->>'endpoint_id')::uuid, s.provider_org_id,
      p->>'environment', s.serving_version_id, p->>'visibility', 'proposed_public',
      (p->>'max_input_tokens')::int, (p->>'max_output_tokens')::int, p_args->>'actor',
      coalesce((p->>'created_at')::timestamptz, infrx.now()))
    returning * into d;
  exception when unique_violation then
    perform infrx.refuse('state_conflict', 'a revision already holds this id');
  when check_violation or not_null_violation or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a proposal is a public prod revision on the '
                         'provider''s prod endpoint, proposed by a named actor');
  end;
  perform infrx.lab_control_audit(s.provider_org_id, 'lab_propose', p_args->>'actor',
    d.deployment_revision_id::text, jsonb_build_object('source', s.deployment_revision_id));
  return to_jsonb(d);
end $$;
