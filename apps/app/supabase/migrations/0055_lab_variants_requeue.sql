-- lab-sql LW7 (COMPOSITION-6-a6cbba5.md "follow-ups"): WR-C6-VARIANTS and WR-C6-REQUEUE.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
--   WR-C6-VARIANTS (R3's listing for `/lab/v1/optimizations`, which answers 503 until then):
--     lab_optimization_variants {provider_org_id} -> [{variant_ref, base_serving_ref,
--         variant_serving_ref, changes, comparison}]: the provider's `lab:variant` records
--         (`lab.optimization_variant.1`, 0029), oldest first, each with its NEWEST stored
--         `infrx.variant_comparison.1` (0040: outcome, reasons, report digest, performance,
--         claim) plus its `comparison_digest`, or null when never compared. Another
--         provider's variants never appear (R227); an unknown provider reads [].
--   WR-C6-REQUEUE (R243: a failed import id stays terminal):
--     lab_import_jobs.requeued_from: the failed job a job was requeued from (one successor
--         per failed job: unique).
--     lab_import_requeue {job_id, new_job_id, provider_org_id, actor}: the provider's
--         `failed` job (another provider's or an unknown id: `not_found`) -> a NEW `queued`
--         job `new_job_id` with the same import spec, the requeuer as its creator and actor,
--         recording its predecessor; the datasets role claims it like any job. A replay
--         answers the one successor unchanged; a queued, running or succeeded job is
--         `state_conflict` naming its state; a missing actor `invalid_request`; a new id
--         another job holds `state_conflict`. The gateway copies the upload's rows to the
--         new id before calling this (`imports.work` reads rows by job id).
--
-- EXECUTE service_role only through 0004's defaults (SECURITY DEFINER).
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_import_requeue(jsonb), infrx.lab_optimization_variants(jsonb);
--   alter table infrx.lab_import_jobs drop column requeued_from.
--
-- Re-runnable: `if not exists`, `create or replace`.

alter table infrx.lab_import_jobs add column if not exists requeued_from uuid unique
  references infrx.lab_import_jobs on delete restrict;

create or replace function infrx.lab_optimization_variants(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('variant_ref', r.ref,
      'base_serving_ref', r.body::jsonb->>'base_serving_ref',
      'variant_serving_ref', r.body::jsonb->>'variant_serving_ref',
      'changes', r.body::jsonb->'changes', 'comparison', c.doc)
      order by r.published_at, r.ref), '[]')
    from infrx.lab_records r
    left join lateral (
      select v.body::jsonb || jsonb_build_object('comparison_digest', v.comparison_digest) doc
        from infrx.lab_variant_comparisons v
       where v.variant_ref = r.ref
       order by v.stored_at desc, v.comparison_digest desc limit 1) c on true
   where r.kind = 'variant'
     and r.provider_org_id = (p_args->>'provider_org_id')::uuid
$$;

create or replace function infrx.lab_import_requeue(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.lab_import_jobs%rowtype;
begin
  select * into j from infrx.lab_import_jobs where job_id = (p_args->>'job_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such import job for this provider');
  end if;
  if j.state <> 'failed' then
    perform infrx.refuse('state_conflict', 'only a failed import job is requeued; this one '
                         'is ' || j.state);
  end if;
  begin
    insert into infrx.lab_import_jobs (job_id, provider_org_id, spec, created_by,
                                       requeued_from)
    values ((p_args->>'new_job_id')::uuid, j.provider_org_id,
            jsonb_set(j.spec, '{actor}', to_jsonb(p_args->>'actor')), p_args->>'actor',
            j.job_id)
    on conflict do nothing;                     -- a replay (or its race): the one successor
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a requeue names a new job id and a named actor');
  end;
  select * into j from infrx.lab_import_jobs where requeued_from = j.job_id;
  if not found then
    perform infrx.refuse('state_conflict', 'that new job id is another job''s');
  end if;
  return infrx.lab_import_job_json(j) || jsonb_build_object('requeued_from', j.requeued_from);
end $$;
