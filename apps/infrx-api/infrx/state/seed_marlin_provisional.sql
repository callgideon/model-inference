-- Operator seed: the Marlin-2B registry rows and a PROVISIONAL CREDIT rate card, so the
-- App's admission path exists without Lab (research/plan/11 §D1R item 3; 02: "until Lab
-- exists, operators seed the identical records").
--
-- NOT a migration: it lives outside apps/app/supabase/migrations/ so no `db push` applies
-- it. An operator runs it deliberately, once the migrations through 0008 are installed:
--
--   psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 \
--        -f apps/infrx-api/infrx/state/seed_marlin_provisional.sql
--
-- Idempotent: fixed identities, `on conflict do nothing`; a second run changes nothing.
-- It does NOT enable anything - `infrx.feature_flags.credit_admission` stays as it is.
--
-- The records are the F2P v2 fixtures verbatim (`infrx/contracts/fixtures/v2/`:
-- serving_revision, deployment_revision_public/_private_dev, rate_card_marlin,
-- data_access_policy), which carry the S2M-measured artifact digests.
--
-- PROVISIONAL - P-01 pending (research/plan/15-pending-inputs.md): 400 / 1,200 CREDIT per
-- million input/output tokens is F2P's unit-change of the v1 fixture ratio, NOT an
-- operator-approved launch price. The card says so twice: `provisional = true` and
-- `approved_by = 'provisional - P-01 pending'`. Replacing it is a NEW card and a new
-- listing version, never an edit. No exchange rate is implied.

begin;

insert into infrx.provider_orgs (provider_org_id, slug, display_name, created_by)
values ('b0000001-0000-4000-8000-000000000001', 'nemostation', 'NemoStation',
        'operator-seed')
on conflict do nothing;

-- The catalog row from 0002 gets its v2 identity and owner - only while nothing
-- references it yet (a model with versions keeps its identity: 0007's composite key).
update public.models
   set model_uuid = 'd0000001-0000-4000-8000-000000000001',
       provider_org_id = 'b0000001-0000-4000-8000-000000000001'
 where id = 'nemostation/marlin-2b'
   and (model_uuid, provider_org_id) is distinct from
       ('d0000001-0000-4000-8000-000000000001'::uuid,
        'b0000001-0000-4000-8000-000000000001'::uuid)
   and not exists (select 1 from infrx.model_versions v where v.model_id = models.model_uuid);

insert into infrx.model_versions (model_version_id, model_id, provider_org_id, model_repo,
  model_commit, weight_shard_digests, tokenizer_digest, chat_template_digest, digest_source,
  created_by, created_at)
values ('d0000002-0000-4000-8000-000000000002', 'd0000001-0000-4000-8000-000000000001',
        'b0000001-0000-4000-8000-000000000001', 'NemoStation/Marlin-2B',
        'fd111fca4fc7897876fb0d7e9df22ca5ac8ab965',
        array['sha256:5d78fa4dbd856dc89c01b99ffa92072fe31b8a1e6b31e87893734c80304983b7',
              'sha256:01d40ec9ccf4c2ad8e755604468dd6ee4a5c6551553e5739a03beb4c0673d0db'],
        'sha256:06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523',
        'sha256:273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80',
        'served_bytes', 'operator-seed', '2026-09-01T00:00:00Z')
on conflict do nothing;

insert into infrx.serving_versions (serving_version_id, model_version_id, model_id,
  provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version,
  runtime_image_ref, runtime_image_digest, engine_options_digest, precision, capability,
  created_by, created_at)
values ('d0000003-0000-4000-8000-000000000003', 'd0000002-0000-4000-8000-000000000002',
        'd0000001-0000-4000-8000-000000000001', 'b0000001-0000-4000-8000-000000000001',
        '2026-09-01', 'marlin2b.chat.v1', 'marlin2b.video.v1',
        'vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42',
        null,     -- runtime_image_digest: a NEW serving version records it (R76)
        'sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147',
        'bfloat16',
        '{"api_family": "chat_completions", "billing_meter": "tokens-v1",
          "input_modalities": ["text", "video"], "input_schema_ref": "infrx.request.chat.v1",
          "output_modalities": ["text"], "output_schema_ref": "infrx.response.chat.v1",
          "preprocessing_profile_ref": "marlin2b.video.v1", "schema_version": 2,
          "stream_input": false, "stream_output": true, "structured_output": false,
          "tools": false}'::jsonb,
        'operator-seed', '2026-09-01T00:00:00Z')
on conflict do nothing;

insert into infrx.endpoints (endpoint_id, provider_org_id, name, environment, created_by)
values ('c0000001-0000-4000-8000-000000000001', 'b0000001-0000-4000-8000-000000000001',
        'marlin-2b', 'dev', 'operator-seed'),
       ('c0000002-0000-4000-8000-000000000002', 'b0000001-0000-4000-8000-000000000001',
        'marlin-2b', 'prod', 'operator-seed')
on conflict do nothing;

insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, provider_org_id,
  environment, serving_version_id, visibility, state, max_input_tokens, max_output_tokens,
  created_by, created_at)
values ('c0000003-0000-4000-8000-000000000003', 'c0000001-0000-4000-8000-000000000001',
        'b0000001-0000-4000-8000-000000000001', 'dev', 'd0000003-0000-4000-8000-000000000003',
        'private', 'ready_private', 30720, 2048, 'operator-seed', '2026-09-01T00:00:00Z'),
       ('c0000004-0000-4000-8000-000000000004', 'c0000002-0000-4000-8000-000000000002',
        'b0000001-0000-4000-8000-000000000001', 'prod', 'd0000003-0000-4000-8000-000000000003',
        'public', 'active', 30720, 2048, 'operator-seed', '2026-09-01T00:00:00Z')
on conflict do nothing;

insert into infrx.rate_card_versions (rate_card_version, model_id, deployment_revision_id,
  serving_version_id, input_rate_per_million, output_rate_per_million, effective_at,
  approved_by, provisional)
values ('rc_marlin2b_2026_09_provisional', 'd0000001-0000-4000-8000-000000000001',
        'c0000004-0000-4000-8000-000000000004', 'd0000003-0000-4000-8000-000000000003',
        400.00000000, 1200.00000000, '2026-09-01T00:00:00Z', 'provisional - P-01 pending',
        true)
on conflict do nothing;

insert into infrx.data_access_policies (policy_version, effective_at, created_by)
values ('dap_2026_09_01', '2026-09-01T00:00:00Z', 'operator-seed')
on conflict do nothing;

insert into infrx.catalog_listings (public_model_id, version, model_id,
  deployment_revision_id, serving_version_id, rate_card_version, effective_at, approved_by)
values ('nemostation/marlin-2b', 1, 'd0000001-0000-4000-8000-000000000001',
        'c0000004-0000-4000-8000-000000000004', 'd0000003-0000-4000-8000-000000000003',
        'rc_marlin2b_2026_09_provisional', '2026-09-01T00:00:00Z',
        'provisional - P-01 pending')
on conflict do nothing;

commit;
