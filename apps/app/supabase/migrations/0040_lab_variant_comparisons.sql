-- WR-R3-2 (wave-5 LW2 request from the rollout-control lane's R3; lane lab-sql, D7): R3's
-- `infrx.variant_comparison.1` stored beside the B2 report it rests on - write-once, keyed by
-- the sha256 of the stored RFC 8785 bytes (the same content addressing as 0034's
-- `lab_eval_reports`), bound to the provider's own published optimization variant record and
-- its own report (EVAL-REPRO, PIPELINE-LINEAGE).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
--   lab_variant_comparisons  one per comparison digest: the variant ref (a `lab:variant`
--                            record of the provider), the report digest (the provider's B2
--                            report), the outcome (equivalent | not_equivalent | inconclusive
--                            | rejected), whether an optimization is claimed, the bytes.
--
-- RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults):
--   lab_put_variant_comparison {provider_org_id, actor, body}: the body is RFC 8785 JSON of
--       an `infrx.variant_comparison.1`; its variant and report are the provider's
--       (`not_found`); a claim needs an `equivalent` outcome (`invalid_request`); the same
--       bytes again are the same row -> {comparison_digest}.
--   lab_variant_comparisons {provider_org_id, report_digest}: the comparisons resting on
--       that report, oldest first ([] for another provider's report).
--
-- ROLLBACK (this file alone): drop function infrx.lab_variant_comparisons(jsonb),
--   infrx.lab_put_variant_comparison(jsonb); drop table infrx.lab_variant_comparisons.
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_variant_comparisons (
  comparison_digest text primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  variant_ref text not null references infrx.lab_records on delete restrict,
  report_digest text not null references infrx.lab_eval_reports on delete restrict,
  outcome text not null
    check (outcome in ('equivalent', 'not_equivalent', 'inconclusive', 'rejected')),
  optimization_claimed boolean not null,
  body text not null check (length(body) <= 1048576),
  stored_by text not null check (length(btrim(stored_by)) between 1 and 200),
  stored_at timestamptz not null default infrx.now(),
  constraint lab_variant_comparisons_content_addressed
    check (comparison_digest = 'sha256:' || encode(sha256(convert_to(body, 'UTF8')), 'hex')),
  constraint lab_variant_comparisons_claim_is_equivalence
    check (not optimization_claimed or outcome = 'equivalent')
);
create index if not exists lab_variant_comparisons_report
  on infrx.lab_variant_comparisons (report_digest, stored_at);

create or replace trigger lab_variant_comparisons_immutable before update or delete
  on infrx.lab_variant_comparisons for each row execute function infrx.forbid_update_delete();
create or replace trigger lab_variant_comparisons_no_truncate before truncate
  on infrx.lab_variant_comparisons for each statement execute function infrx.forbid_truncate();
alter table infrx.lab_variant_comparisons enable row level security;
revoke all on infrx.lab_variant_comparisons from public, anon, authenticated, service_role;
grant select on infrx.lab_variant_comparisons to service_role;

create or replace function infrx.lab_put_variant_comparison(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_digest text := 'sha256:' || encode(sha256(convert_to(p_args->>'body', 'UTF8')), 'hex');
  v_doc jsonb;
begin
  begin
    v_doc := (p_args->>'body')::jsonb;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'the body is not JSON');
  end;
  if v_doc->>'schema' is distinct from 'infrx.variant_comparison.1' then
    perform infrx.refuse('invalid_request', 'not an infrx.variant_comparison.1');
  end if;
  if not exists (select 1 from infrx.lab_records r where r.ref = v_doc->>'variant_ref'
                  and r.kind = 'variant' and r.provider_org_id = v_provider)
     or not exists (select 1 from infrx.lab_eval_reports e
                     where e.report_digest = v_doc->>'report_digest'
                       and e.provider_org_id = v_provider) then
    perform infrx.refuse('not_found', 'a comparison rests on this provider''s variant and '
                         'report');
  end if;
  begin
    insert into infrx.lab_variant_comparisons (comparison_digest, provider_org_id, variant_ref,
      report_digest, outcome, optimization_claimed, body, stored_by)
    values (v_digest, v_provider, v_doc->>'variant_ref', v_doc->>'report_digest',
      v_doc->>'outcome', (v_doc->>'optimization_claimed')::boolean, p_args->>'body',
      p_args->>'actor')
    on conflict (comparison_digest) do nothing;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a comparison has an outcome, claims an '
                         'optimization only when equivalent, and names who stored it');
  end;
  return jsonb_build_object('comparison_digest', v_digest);
end $$;

create or replace function infrx.lab_variant_comparisons(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('comparison_digest', c.comparison_digest,
      'body', c.body, 'stored_by', c.stored_by, 'stored_at', c.stored_at)
      order by c.stored_at, c.comparison_digest), '[]')
    from infrx.lab_variant_comparisons c
   where c.report_digest = p_args->>'report_digest'
     and c.provider_org_id = (p_args->>'provider_org_id')::uuid
$$;
