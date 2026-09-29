-- E8L-F1 (WR-E8L-2, lane lab-sql-lw5): one serving-ref identity for a rollout candidate.
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- Bug (research/plan/evidence/e/E8L-2334e6a.md "E8L-F1"): 0043's `release_active` resolved a
-- policy candidate's `lab:serving:<provider>:<id>@sha256:<d>` with `<id>` read as
-- serving_version_id and `<d>` ignored. L3's `operations.serving_ref` (R188, 08-contracts
-- §10) names `<id>` = deployment_revision_id and `<d>` = the JCS (RFC 8785) digest of the
-- served revision, i.e. `sha256(canonical(ServingRevision.model_dump(mode="json")))` in
-- `infrx/contracts/v2/records.py` + `infrx/contracts/lab/records.py::canonical`. The two
-- identity spaces disagree, so `emergency_rollback` records a decision R2's `_converge` (over
-- the alias L3 actually moves) never recognises as "the candidate" - the alias never rolls
-- back (E8L k06).
--
-- Ruling (proposed as R191 in `research/plan/e/E8L-2334e6a.md`, adopted here; run through
-- R201, not renumbered by this lane): a rollout policy's candidate/baseline `serving_ref` is
-- L3's `operations.serving_ref`. `release_active` resolves a candidate by deployment revision
-- (its serving version gives the R62 label) and refuses a ref whose digest is not that
-- revision's.
--
-- `infrx.lab_serving_ref_digest(serving_version_id)`: the sha256 hex of the JCS bytes of the
-- `ServingRevision` a `serving_version_id` names, built field-for-field from
-- `infrx.serving_versions` + `infrx.model_versions` + `public.models` (0043's `_SERVING`
-- join, `apps/infrx-api/infrx/state/operations.py`) the same way
-- `ServingRevision.model_dump(mode="json")` does. `schema_version` (currently 2,
-- `infrx/contracts/v2/records.py:SCHEMA_VERSION`) and `CapabilityRecord`'s own defaults
-- (`billing_meter`, `api_family`, `stream_input`, `tools`, `structured_output`, its own
-- `schema_version`) are filled the way pydantic validation fills them when the stored
-- `capability` jsonb omits them, matching the CHECK on `infrx.serving_versions.capability`
-- that only requires `billing_meter`/`api_family` to be present. Verified byte-for-byte
-- against `infrx.lab.control.operations.serving_ref` for two field combinations (all optional
-- fields null and a zero-microsecond timestamp; every optional field set, two weight shards,
-- every `CapabilityRecord` flag non-default and a microsecond timestamp) - see the lane's
-- evidence file for the reproduction. ponytail: this reimplements one Python record's JSON
-- shape in SQL by hand; it is the whole risk surface of this migration. `SCHEMA_VERSION`
-- and `ServingRevision`/`CapabilityRecord`'s field sets are duplicated knowledge - a change to
-- either in `infrx/contracts/v2/records.py` without a matching change here is silently wrong
-- (`tests/d/test_d9_rollout_serving_ref.py` pins the current shape so it fails loudly instead
-- if the two drift; there is no general JCS-in-SQL to fall back on without a much larger
-- change touching every lane that constructs a `serving_ref` fixture).
--
-- `infrx.lab_serving_ref(deployment_revision_id)`: the full ref of the deployment's current
-- serving revision, or null if no such deployment exists. `release_active` and any lane
-- constructing a real candidate/baseline ref (own paths only) call this one function, so the
-- ref text is built in exactly one place.
--
-- ROLLBACK (this file alone): `create or replace function infrx.release_active` back to
-- 0043's body (below, kept verbatim in this comment for the revert); drop
-- infrx.lab_serving_ref(uuid), infrx.lab_serving_ref_digest(uuid), infrx.lab_rfc3339(timestamptz).
--   0043's body: resolve v_model/v_endpoint by listing; the running rollout at v_endpoint;
--   for each candidate, `select sv.revision_label into v_label from infrx.serving_versions sv
--   where sv.serving_version_id::text = (infrx.lab_ref_parts(c->>'serving_ref'))[3] and
--   sv.provider_org_id = o.provider_org_id and sv.model_id = v_model`; refuse state_conflict
--   if v_label is null; else accumulate `c->>'serving_ref' -> requested_model||'@'||v_label`.

create or replace function infrx.lab_rfc3339(p_ts timestamptz) returns text
language sql immutable set search_path = infrx, public, pg_temp as $$
  -- `datetime.isoformat()` (Python): no fractional seconds at all when microsecond = 0,
  -- else exactly six digits, zero-padded. `to_char`'s `US` is already zero-padded to 6.
  select to_char(p_ts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
    || case when to_char(p_ts at time zone 'UTC', 'US') = '000000' then ''
            else '.' || to_char(p_ts at time zone 'UTC', 'US') end
    || 'Z'
$$;

create or replace function infrx.lab_serving_ref_digest(p_serving_version_id uuid)
returns text language sql stable set search_path = infrx, public, pg_temp as $$
  select encode(sha256(convert_to(
    '{'
    || '"adapter_digest":' || coalesce(to_json(v.adapter_digest)::text, 'null') || ','
    || '"capability":{'
      || '"api_family":' || to_json(coalesce(s.capability->>'api_family', 'chat_completions')) || ','
      || '"billing_meter":' || to_json(coalesce(s.capability->>'billing_meter', 'tokens-v1')) || ','
      || '"input_modalities":' || (select coalesce('[' || string_agg(to_json(e)::text, ',' order by o) || ']', '[]')
           from jsonb_array_elements_text(s.capability->'input_modalities') with ordinality as t(e, o)) || ','
      || '"input_schema_ref":' || to_json(s.capability->>'input_schema_ref') || ','
      || '"output_modalities":' || (select coalesce('[' || string_agg(to_json(e)::text, ',' order by o) || ']', '[]')
           from jsonb_array_elements_text(s.capability->'output_modalities') with ordinality as t(e, o)) || ','
      || '"output_schema_ref":' || to_json(s.capability->>'output_schema_ref') || ','
      || '"preprocessing_profile_ref":' || to_json(s.capability->>'preprocessing_profile_ref') || ','
      || '"schema_version":' || coalesce(s.capability->>'schema_version', '2') || ','
      || '"stream_input":' || coalesce(s.capability->>'stream_input', 'false') || ','
      || '"stream_output":' || (s.capability->>'stream_output') || ','
      || '"structured_output":' || coalesce(s.capability->>'structured_output', 'false') || ','
      || '"tools":' || coalesce(s.capability->>'tools', 'false')
    || '},'
    || '"chat_template_digest":' || to_json(v.chat_template_digest) || ','
    || '"created_at":' || to_json(infrx.lab_rfc3339(s.created_at)) || ','
    || '"digest_source":' || to_json(v.digest_source) || ','
    || '"engine_options_digest":' || to_json(s.engine_options_digest) || ','
    || '"model_commit":' || to_json(v.model_commit) || ','
    || '"model_id":' || to_json(s.model_id::text) || ','
    || '"model_repo":' || to_json(v.model_repo) || ','
    || '"model_version_id":' || to_json(s.model_version_id::text) || ','
    || '"precision":' || to_json(s.precision) || ','
    || '"preprocessor_profile_version":' || to_json(s.preprocessor_profile_version) || ','
    || '"prompt_harness_ref":' || to_json(s.prompt_harness_ref) || ','
    || '"provider_org_id":' || to_json(s.provider_org_id::text) || ','
    || '"public_model_id":' || to_json(m.id) || ','
    || '"revision_label":' || to_json(s.revision_label) || ','
    || '"runtime_image_digest":' || coalesce(to_json(s.runtime_image_digest)::text, 'null') || ','
    || '"runtime_image_ref":' || to_json(s.runtime_image_ref) || ','
    || '"schema_version":2,'
    || '"serving_version_id":' || to_json(s.serving_version_id::text) || ','
    || '"tokenizer_digest":' || to_json(v.tokenizer_digest) || ','
    || '"weight_shard_digests":' || (select '[' || string_agg(to_json(e)::text, ',' order by o) || ']'
         from unnest(v.weight_shard_digests) with ordinality as t(e, o))
    || '}'
  , 'UTF8')), 'hex')
  from infrx.serving_versions s
  join infrx.model_versions v on v.model_version_id = s.model_version_id
  join public.models m on m.model_uuid = s.model_id
  where s.serving_version_id = p_serving_version_id
$$;

create or replace function infrx.lab_serving_ref(p_deployment_revision_id uuid)
returns text language sql stable set search_path = infrx, public, pg_temp as $$
  select 'lab:serving:' || d.provider_org_id || ':' || d.deployment_revision_id || '@sha256:'
         || infrx.lab_serving_ref_digest(d.serving_version_id)
  from infrx.deployment_revisions d
  where d.deployment_revision_id = p_deployment_revision_id
$$;

create or replace function infrx.release_active(requested_model text)
returns table (record jsonb, policy_ref text, revisions jsonb, shadow_limit int)
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  o infrx.lab_rollouts%rowtype;
  v_model uuid;
  v_endpoint uuid;
  v_doc jsonb;
  v_revisions jsonb := '{}';
  c jsonb;
  v_dep_id text;
  v_computed text;
  v_label text;
begin
  select l.model_id, d.endpoint_id into v_model, v_endpoint
    from infrx.catalog_listings l
    join infrx.deployment_revisions d on d.deployment_revision_id = l.deployment_revision_id
   where l.public_model_id = requested_model and l.effective_at <= infrx.now()
   order by l.version desc limit 1;
  select * into o from infrx.lab_rollouts x
   where x.endpoint_id = v_endpoint and x.state = 'running';
  if o.policy_id is null then
    return;
  end if;
  select r.body::jsonb into v_doc from infrx.lab_records r where r.ref = o.policy_ref;
  for c in select * from jsonb_array_elements(v_doc->'candidates') loop
    v_dep_id := (infrx.lab_ref_parts(c->>'serving_ref'))[3];
    v_computed := case when v_dep_id is null then null
                       else infrx.lab_serving_ref(v_dep_id::uuid) end;
    select sv.revision_label into v_label
      from infrx.deployment_revisions d2
      join infrx.serving_versions sv on sv.serving_version_id = d2.serving_version_id
     where d2.deployment_revision_id::text = v_dep_id
       and d2.provider_org_id = o.provider_org_id and sv.model_id = v_model;
    if v_label is null or v_computed is distinct from (c->>'serving_ref') then
      perform infrx.refuse('state_conflict', 'candidate ' || (c->>'serving_ref')
                           || ' is not a published revision of ' || requested_model);
    end if;
    v_revisions := v_revisions || jsonb_build_object(c->>'serving_ref',
                                                     requested_model || '@' || v_label);
  end loop;
  return query select v_doc, o.policy_ref, v_revisions, o.shadow_limit;
end $$;

revoke all on function infrx.lab_rfc3339(timestamptz), infrx.lab_serving_ref_digest(uuid),
  infrx.lab_serving_ref(uuid) from public, anon, authenticated;
grant execute on function infrx.lab_rfc3339(timestamptz), infrx.lab_serving_ref_digest(uuid),
  infrx.lab_serving_ref(uuid) to service_role, infrx_runtime;
