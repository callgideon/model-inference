-- lab-sql LW9 (LAB-SQL-LW7 "Carried", COMPOSITION-7 open items): WR-LW7-3a and WR-C7-TALLY.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
--   WR-LW7-3a (R3 persists both revision identities of an optimization variant; R252 (b)
--   until this is hosted):
--     lab_variant_identities  one per `lab:variant` record: R3's base and variant `Identity`
--         (as `model_dump(mode="json")` writes it), each shaped as the Lab's VARIANT reads it:
--         engine, engine_version, hardware, quantization (strings), capabilities (strings).
--         Write-once (immutable rows).
--     lab_put_variant_identities {provider_org_id, variant_ref, base, variant, actor}: the
--         provider's own variant (another provider's or none: `not_found`, R227); a shape
--         the Lab cannot read or no actor: `invalid_request`; the same identities again are
--         the same answer, different ones `state_conflict`; a pair whose serving refs
--         (`lab_identity_serving_ref`, R3's `serving_ref` in SQL) are not the variant's
--         `invalid_request` (F6, merge #65) -> {variant_ref, base, variant}.
--     lab_optimization_variant_listing {provider_org_id}: 0055's `lab_optimization_variants`
--         in its order, each row with `base` and `variant` (null while R3 has not stored
--         them). 0055's function is not redefined (its mutants stay live).
--   WR-C7-TALLY (the releases page's Progress.assignments):
--     lab_release_tally {policy_ref}: [{serving_ref, pinned_by, requests}] of the revision's
--         `lab_rollout_assignments` over terminal jobs (0054's `requests`: succeeded, failed,
--         cancelled, expired), by serving then pin; the rows sum to 0054's two arms. None
--         (or an unknown revision) is [].
--
-- EXECUTE: service_role through 0004's defaults (SECURITY DEFINER). R251: the control unit's
-- login `infrx_lab_control` also executes the route reads - the listing, the tally, and
-- 0054's `lab_release_live`, which `/lab/v1/releases` calls since WR-LIVE-PAGE (composition-7)
-- - never R3's write.
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_release_tally(jsonb), infrx.lab_optimization_variant_listing(jsonb),
--   infrx.lab_put_variant_identities(jsonb); drop table infrx.lab_variant_identities;
--   drop function infrx.lab_identity_shaped(jsonb), infrx.lab_identity_serving_ref(text, jsonb);
--   revoke execute on function infrx.lab_release_live(jsonb) from infrx_lab_control.
--
-- Re-runnable: `if not exists`, `create or replace`.

create or replace function infrx.lab_identity_shaped(p jsonb) returns boolean
language sql immutable set search_path = pg_catalog as $$
  select coalesce(jsonb_typeof(p->'engine') = 'string'
    and jsonb_typeof(p->'engine_version') = 'string'
    and jsonb_typeof(p->'hardware') = 'string'
    and jsonb_typeof(p->'quantization') = 'string'
    and jsonb_typeof(p->'capabilities') = 'array'
    and not jsonb_path_exists(p, '$.capabilities[*] ? (@.type() != "string")'), false)
$$;

-- F6 (merge #65): R3's `serving_ref(provider, identity)` - the JCS (RFC 8785) sha256 of the
-- identity's JSON dump, its first 32 hex digits as a version-4 UUID. ponytail: JCS of a flat
-- object of strings and string arrays only (R3's `Identity`); any other value keeps its jsonb
-- spelling, so such an identity never matches a ref R3 registered.
create or replace function infrx.lab_identity_serving_ref(p_provider text, p jsonb)
returns text language sql immutable set search_path = pg_catalog as $$
  select 'lab:serving:' || p_provider || ':' || substr(h, 1, 8) || '-' || substr(h, 9, 4)
      || '-4' || substr(h, 14, 3) || '-'
      || substr('89ab', (('x' || substr(h, 17, 1))::bit(4)::int & 3) + 1, 1)
      || substr(h, 18, 3) || '-' || substr(h, 21, 12) || '@sha256:' || h
    from (select encode(sha256(convert_to('{' || string_agg(to_jsonb(k)::text || ':' ||
            case jsonb_typeof(x) when 'array' then '[' || coalesce((select string_agg(
              e::text, ',' order by n) from jsonb_array_elements(x) with ordinality a(e, n)),
              '') || ']' else x::text end, ',' order by k collate "C") || '}', 'UTF8')), 'hex') h
            from jsonb_each(case when jsonb_typeof(p) = 'object' then p else '{}' end) j(k, x)
         ) d
$$;

create table if not exists infrx.lab_variant_identities (
  variant_ref text primary key references infrx.lab_records on delete restrict,
  base jsonb not null,
  variant jsonb not null,
  stored_by text not null check (length(btrim(stored_by)) between 1 and 200),
  stored_at timestamptz not null default infrx.now(),
  constraint lab_variant_identities_shaped
    check (infrx.lab_identity_shaped(base) and infrx.lab_identity_shaped(variant))
);

create or replace trigger lab_variant_identities_immutable before update or delete
  on infrx.lab_variant_identities for each row execute function infrx.forbid_update_delete();
create or replace trigger lab_variant_identities_no_truncate before truncate
  on infrx.lab_variant_identities for each statement execute function infrx.forbid_truncate();
alter table infrx.lab_variant_identities enable row level security;
revoke all on infrx.lab_variant_identities from public, anon, authenticated, service_role;
grant select on infrx.lab_variant_identities to service_role;

create or replace function infrx.lab_put_variant_identities(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_ref text := p_args->>'variant_ref';
  v_row infrx.lab_variant_identities%rowtype;
  v_body jsonb;
begin
  if not exists (select 1 from infrx.lab_records r where r.ref = v_ref and r.kind = 'variant'
                  and r.provider_org_id = (p_args->>'provider_org_id')::uuid) then
    perform infrx.refuse('not_found', 'no such optimization variant for this provider');
  end if;
  begin
    insert into infrx.lab_variant_identities (variant_ref, base, variant, stored_by)
    values (v_ref, p_args->'base', p_args->'variant', p_args->>'actor')
    on conflict (variant_ref) do nothing;       -- a replay (or its race): the stored pair
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'both identities name engine, engine_version, '
                         'hardware, quantization and capabilities, and who stored them');
  end;
  select * into v_row from infrx.lab_variant_identities where variant_ref = v_ref;
  if (v_row.base, v_row.variant) is distinct from (p_args->'base', p_args->'variant') then
    perform infrx.refuse('state_conflict', 'this variant''s identities are stored and differ');
  end if;
  select r.body::jsonb into v_body from infrx.lab_records r where r.ref = v_ref;
  if (v_body->>'base_serving_ref', v_body->>'variant_serving_ref') is distinct from
     (infrx.lab_identity_serving_ref(v_body->>'provider_org_id', p_args->'base'),
      infrx.lab_identity_serving_ref(v_body->>'provider_org_id', p_args->'variant')) then
    perform infrx.refuse('invalid_request', 'the identities are not this variant''s serving refs');
  end if;
  return jsonb_build_object('variant_ref', v_ref, 'base', v_row.base, 'variant', v_row.variant);
end $$;

create or replace function infrx.lab_optimization_variant_listing(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(e.doc || jsonb_build_object('base', i.base, 'variant', i.variant)
      order by e.n), '[]')
    from jsonb_array_elements(infrx.lab_optimization_variants(p_args)) with ordinality e(doc, n)
    left join infrx.lab_variant_identities i on i.variant_ref = e.doc->>'variant_ref'
$$;

create or replace function infrx.lab_release_tally(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('serving_ref', s.serving_ref,
      'pinned_by', s.pinned_by, 'requests', s.requests) order by s.serving_ref, s.pinned_by),
      '[]')
    from (select a.serving_ref, a.pinned_by, count(*) requests
            from infrx.lab_rollout_assignments a
            join infrx.jobs j on j.request_id = a.request_id
           where a.policy_ref = p_args->>'policy_ref'
             and j.state in ('succeeded', 'failed', 'cancelled', 'expired')
           group by a.serving_ref, a.pinned_by) s
$$;

grant execute on function infrx.lab_optimization_variant_listing(jsonb),
  infrx.lab_release_tally(jsonb), infrx.lab_release_live(jsonb) to infrx_lab_control;
