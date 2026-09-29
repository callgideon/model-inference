-- WR-C6-LIVE (lane lab-live, R244): R2's `Live` for one policy revision, read per arm from R1's
-- recorded assignments and the admitted jobs they name. Additive: one function; no table,
-- column, constraint, grant change or existing function is touched.
-- LOCAL-ONLY (R150/R151/R201): never applied hosted; the number is the next free at merge.
--
--   lab_release_live {policy_ref}: one row per arm (`baseline`, then `candidate`: every
--       assignment not on the revision's baseline_ref is the candidate arm) over
--       `lab_rollout_assignments` (this revision's) joined to `infrx.jobs`:
--         requests          terminal jobs (succeeded | failed | cancelled | expired);
--         errors            failed + expired (a cancel is the user's, not an error);
--         p99_ms            percentile_disc(0.99) of settled_at - admitted_at, in ms (null
--                           without a terminal job);
--         spent {unit, value}  the settled spend in the unit the jobs settled in: CREDIT = the
--                           job's `inference_debit` (a CREDIT job's v1 debit is 0, R87), USD =
--                           a legacy_usd job's debit. Jobs of both regimes are refused
--                           (`invalid_request`), never summed or converted;
--         quality_covered   requests with an operator or customer feedback row (T2F; a
--                           judge's row is not coverage);
--         candidate_healthy every candidate's L3 deployment revision (the R188 ref's
--                           deployment_revision_id) is ready: `ready_private` or `active`;
--         observed_until    infrx.now(), the database clock at read.
--       No assignment naming an admitted job: `[]` (nothing observed - the rollout pass holds,
--       never evaluates zeros). An unknown revision: `not_found`.
--
-- RPC: SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults (the Lab rollout
-- worker's pool, like 0053's listings).
--
-- ROLLBACK (this file alone; nothing references it): drop function infrx.lab_release_live(jsonb).
--
-- Re-runnable: `create or replace`.

create or replace function infrx.lab_release_live(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_doc jsonb;
  v_units text[];
  v_healthy boolean;
begin
  select r.body::jsonb into v_doc from infrx.lab_records r
   where r.ref = p_args->>'policy_ref' and r.kind = 'policy';
  if v_doc is null then
    perform infrx.refuse('not_found', 'no such policy revision');
  end if;
  v_units := array(select distinct j.accounting_regime
                     from infrx.lab_rollout_assignments a
                     join infrx.jobs j on j.request_id = a.request_id
                    where a.policy_ref = p_args->>'policy_ref');
  if cardinality(v_units) = 0 then
    return '[]';
  end if;
  if cardinality(v_units) > 1 then
    perform infrx.refuse('invalid_request', 'this release''s jobs settled in CREDIT and in USD: '
                         'units never mix');
  end if;
  select coalesce(bool_and(coalesce(d.state in ('ready_private', 'active'), false)), false)
    into v_healthy
    from jsonb_array_elements(v_doc->'candidates') c
    left join infrx.deployment_revisions d
      on d.deployment_revision_id::text = (infrx.lab_ref_parts(c->>'serving_ref'))[3];
  return (
    with t as (
      select case when a.serving_ref = v_doc->>'baseline_ref' then 'baseline' else 'candidate' end
               as arm,
             j.request_id, j.state, j.settled_at - j.admitted_at as took,
             case j.accounting_regime
               when 'credit' then coalesce((select -l.amount from infrx.credit_ledger l
                                             where l.kind = 'inference_debit'
                                               and l.request_id = j.request_id), 0)
               else j.debit end as spent,
             exists (select 1 from infrx.feedback f where f.request_id = j.request_id
                        and f.author_role in ('customer', 'operator')) as covered
        from infrx.lab_rollout_assignments a
        join infrx.jobs j on j.request_id = a.request_id
       where a.policy_ref = p_args->>'policy_ref'
         and j.state in ('succeeded', 'failed', 'cancelled', 'expired'))
    select jsonb_agg(row order by arm)
      from (select x.arm, jsonb_build_object(
                'arm', x.arm, 'requests', count(t.request_id),
                'errors', count(*) filter (where t.state in ('failed', 'expired')),
                'p99_ms', ceil(extract(epoch from percentile_disc(0.99)
                               within group (order by t.took)) * 1000)::bigint,
                'spent', jsonb_build_object(
                  'unit', case v_units[1] when 'credit' then 'CREDIT' else 'USD' end,
                  'value', round(coalesce(sum(t.spent), 0), 8)::text),
                'quality_covered', count(*) filter (where t.covered),
                'candidate_healthy', v_healthy,
                'observed_until', infrx.now()) as row
              from (values ('baseline'), ('candidate')) x(arm)
              left join t on t.arm = x.arm
             group by x.arm) s);
end $$;
