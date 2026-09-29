-- WR-LSQ-C2A (lane lab-sql-lw6; COMPOSITION-2-b790f17.md "requests filed"): D6J's listing of
-- `submitted`/`ambiguous` judge runs, for the judge worker role's collect/reconcile pass.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- `0036_lab_judge_ledger.sql` gives J2's `submit()` every move (reserve, begin_submit,
-- record_sent, record_submission, quarantine, release, record_results, settle) and
-- `lab_judge_sweep`'s time-based `submitting` -> `ambiguous` expiry, but no way to find the
-- runs `collect`/`reconcile` need to act on: today "a run is collected by the door that
-- submitted it" (ponytail note in `infrx/lab/workers/__main__.py::_judge`) - the worker
-- process restarting loses every run's collection until its owning request comes back. This
-- adds ONE read RPC, in the run's own provider only, oldest-first (the run most overdue a
-- look comes first):
--
--   lab_judge_runs_in {provider_org_id, states: [...], limit}: up to `limit` of the
--       provider's `lab_judge_runs` rows whose `state` is one of the given values, ordered
--       by `updated_at`. `states` is required and non-empty (the collect pass wants
--       `submitted`, the reconcile pass `ambiguous`; a worker that asks for every state by
--       mistake is refused, not handed an unbounded scan).
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_judge_runs_in(jsonb).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.lab_judge_runs_in(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_states text[] := array(select jsonb_array_elements_text(p_args->'states'));
begin
  if cardinality(v_states) = 0 then
    perform infrx.refuse('invalid_request', 'a judge run listing names at least one state');
  end if;
  return (select coalesce(jsonb_agg(infrx.lab_judge_json(r) order by r.updated_at, r.run_id),
                          '[]')
    from (select * from infrx.lab_judge_runs
           where provider_org_id = (p_args->>'provider_org_id')::uuid
             and state = any(v_states)
           order by updated_at, run_id limit (p_args->>'limit')::int) r);
end $$;
