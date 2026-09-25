-- D10 follow-up: the fenced end of a permanently refused preparation (W5 wiring request 3,
-- R104's third candidate) and the operator's fair, bounded flag writer (G8 V-G8TL-2).
--
--   fail_preparation     `{lease, cause, limits}` -> the committed outcome. A preparation
--                        worker calls it only for a PERMANENT refusal (W5's ruling text:
--                        unsupported_media / request_too_large -> invalid_media,
--                        context_length_exceeded -> preparation_failed; every other refusal
--                        lapses its lease). The cause is checked first (anything else is
--                        invalid_request, nothing read). Then the replay: the SAME lease's
--                        identical call after it committed answers the committed outcome.
--                        Then 0016's fence on the PREPARATION lease (kind, terminal, live
--                        attempt, generation, owner, R29 deadline - terminalized and
--                        answered as data, R39 - then expiry). Then 0016's
--                        `terminalize_no_usage` (state `failed`, no usage, released_free by
--                        R21, the hold released in its own unit, every reservation and the
--                        attempt released, one usage and one trace projection; 0017's
--                        trigger writes the terminal journal event). A lapsed, foreign or
--                        superseded lease is stale_lease; a job another path ended first is
--                        already_terminal; neither changes anything.
--   set_feature_flag     V-G8TL-2 option A: `lock table infrx.feature_flags in exclusive mode`,
--                        then 0006's attributed UPDATE, returning whether it changed the row.
--                        EXCLUSIVE conflicts with the ROW SHARE every admission's
--                        `require_feature ... for share` (0021) takes and still admits plain
--                        reads; a waiting table lock queues NEW share lockers behind it, so a
--                        stream of overlapping admissions can no longer starve the freeze
--                        (a row-level UPDATE joined the MultiXact queue last). SHARE ROW
--                        EXCLUSIVE would not: it does not conflict with ROW SHARE. The
--                        caller's SET LOCAL lock_timeout / statement_timeout bound it.
--
-- The replay key. `jobs.proposal` (0018: the winner's proposal, immutable once settled) is
-- written with the lease's generation and worker BEFORE the terminalization, so only the
-- identical call of the lease that ended the job replays; it can never equal a
-- `terminalize` proposal (that has no `fail_preparation` member).
--
-- LOCK ORDER. `fail_preparation`: the job row (the replay read and the fence take it FOR
-- UPDATE), then its hold, then its wallet - 0016's order. `set_feature_flag`: only the
-- flags table; an admission takes the flag after its scope lock, so no cycle exists (0021).
--
-- ROLLBACK (0022 alone). `drop function infrx.fail_preparation(jsonb)` and
-- `drop function infrx.set_feature_flag(text, boolean, text, text)`; the worker then lapses
-- a permanently refused lease (W5's fallback) and G8 writes the row directly (V-G8TL-1).
-- Jobs already ended keep their outcome: money history is never un-settled.
--
-- Additive and re-runnable.

-- ======================================================= fail_preparation ===
create or replace function infrx.fail_preparation(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_lease jsonb := p_args->'lease';
  v_cause text := p_args->>'cause';
  v_mark jsonb;
  v_refusal jsonb;
  v_reconcile_s float8;
  j infrx.jobs%rowtype;
begin
  if jsonb_typeof(v_lease) is distinct from 'object' or v_lease->>'job_id' is null then
    perform infrx.refuse('invalid_request', 'fail_preparation takes {lease, cause, limits}');
  end if;
  if v_cause is null or v_cause not in ('invalid_media', 'preparation_failed') then
    perform infrx.refuse('invalid_request', coalesce(v_cause, 'null')
                         || ' does not end a preparation');
  end if;
  v_reconcile_s := infrx.lease_limit(p_args, 'unknown_usage_reconcile_s');
  v_mark := jsonb_build_object('cause', v_cause, 'usage', null, 'result_ref', null,
                               'fail_preparation', jsonb_build_object(
                                 'generation', v_lease->'generation',
                                 'worker_id', v_lease->'worker_id'));
  -- The replay: this lease's identical call, after it committed.
  select * into j from infrx.jobs where request_id = (v_lease->>'job_id')::uuid for update;
  if found and j.settled_at is not null and j.proposal = v_mark then
    return infrx.job_admission(j.request_id);
  end if;
  v_refusal := infrx.fence_lease(v_lease, array['preparation'], v_reconcile_s);
  if v_refusal is not null then
    return jsonb_build_object('refusal', v_refusal);
  end if;
  update infrx.jobs set proposal = v_mark where request_id = j.request_id;
  perform infrx.terminalize_no_usage(j.request_id, v_cause, 'failed', v_reconcile_s);
  return infrx.job_admission(j.request_id);
end $$;

revoke all on function infrx.fail_preparation(jsonb) from public, anon, authenticated;
grant execute on function infrx.fail_preparation(jsonb) to service_role, infrx_runtime;

-- ======================================================= set_feature_flag ===
create or replace function infrx.set_feature_flag(p_name text, p_enabled boolean,
                                                  p_actor text, p_reason text)
returns boolean language plpgsql volatile security definer
set search_path = infrx, public, pg_temp as $$
begin
  lock table infrx.feature_flags in exclusive mode;
  update infrx.feature_flags
     set enabled = p_enabled, updated_by = left(p_actor, 200), reason = left(p_reason, 500),
         updated_at = infrx.now()
   where name = p_name and enabled <> p_enabled;
  return found;
end $$;

revoke all on function infrx.set_feature_flag(text, boolean, text, text)
  from public, anon, authenticated;
grant execute on function infrx.set_feature_flag(text, boolean, text, text) to service_role;
