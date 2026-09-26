-- D10-0026: the result object is fenced output (R147; E3C-CELLS F-1). 0014's writer took
-- `{job_id, text}` only, so a generation that had lost its lease could still store its text
-- first and end the live generation `platform_error` (first write wins).
--
--   infrx.put_result   `{job_id, text, lease, limits}`: `lease` is the worker's
--                      `records.Lease` token exactly as `append` takes it, `limits` the
--                      store's `unknown_usage_reconcile_s`. The fence FIRST, as 0017's
--                      `append`: a lease of another job is `invalid_request`; then 0016's
--                      `fence_lease` (inference leases only, R46) refuses a lapsed or
--                      foreign generation, a released attempt or an expired lease
--                      `stale_lease`, an ended job `already_terminal` and an unknown one
--                      `not_found` - all before anything is stored. Past R29's instant the
--                      fence terminalizes the job; that commits (R39), so the answer is
--                      NULL, never a reference, and the port raises `already_terminal`.
--                      After the fence 0014's body, unchanged: the same text replays the
--                      reference, another text is `state_conflict`.
--                      Without a `lease` key it is 0014's write, unchanged: the known-good
--                      rollback targets (4226315, bda1586; P-25) send `{job_id, text}`, and
--                      a rollback to one after this migration must still settle a success.
--                      The worker of this tree always sends its lease.
--
-- Same signature, return type and SECURITY DEFINER: `create or replace` keeps 0014's and
-- 0021's grants (service_role, infrx_runtime). No table, policy or grant changes.
--
-- ORDER. After 0025. Either order with the worker build: a pre-0026 database ignores the
-- extra keys (0014 reads `job_id` and `text`), a pre-0026 worker sends none.
--
-- ROLLBACK (0026 alone; nothing moves): re-run 0014's `create or replace function
-- infrx.put_result` (grants are kept). Stored results stay (immutable, 0014).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.put_result(p_args jsonb) returns text
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_job uuid := (p_args->>'job_id')::uuid;
  v_text text := p_args->>'text';
  v_digest text;
  v_org uuid;
  r infrx.job_results%rowtype;
begin
  if v_text is null then
    perform infrx.refuse('invalid_request', 'put_result takes {job_id, text, lease, limits}');
  end if;
  if p_args ? 'lease' then
    if (p_args->'lease'->>'job_id')::uuid is distinct from v_job then
      perform infrx.refuse('invalid_request', 'the lease does not fence job '
                           || coalesce(v_job::text, 'null'));
    end if;
    -- R29 terminalized the job in the fence: committed (R39), answered as NULL.
    if infrx.fence_lease(p_args->'lease', array['inference'],
                         infrx.lease_limit(p_args, 'unknown_usage_reconcile_s')) is not null then
      return null;
    end if;
  end if;
  select org_id into v_org from infrx.jobs where request_id = v_job;
  if not found then
    perform infrx.refuse('not_found', 'no job ' || v_job);
  end if;
  v_digest := 'sha256:' || encode(sha256(convert_to(v_text, 'UTF8')), 'hex');
  insert into infrx.job_results (request_id, org_id, digest, bytes, body)
  values (v_job, v_org, v_digest, octet_length(v_text), v_text)
  on conflict (request_id) do nothing;
  select * into r from infrx.job_results where request_id = v_job;
  if r.digest <> v_digest then
    perform infrx.refuse('state_conflict', 'job ' || v_job
                         || ' already stored a different result');
  end if;
  return 'infrx-result:' || v_job;
end $$;
