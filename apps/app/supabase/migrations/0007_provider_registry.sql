-- D1R: the minimal provider / model / serving / deployment / listing / rate registry
-- (research/plan/11 §D1R item 3; platforms/07-api-contracts; 06a; R62, R69, R70, R76).
--
-- Additive and re-runnable, like 0006. Every registry row is owned by a provider
-- organization through a foreign key; `public.models.provider` stays DISPLAY text and is
-- never a key. Pinned identities are immutable after insert: a changed rate, alias,
-- promotion or rollback is a NEW row, so nothing admitted earlier can be re-priced or
-- re-pointed (CREDIT-RATE). Operators seed these rows without Lab through
-- `apps/infrx-api/infrx/state/seed_marlin_provisional.sql`; Lab later adds provider
-- workflows around the same relations.

-- ========================================================= provider identity ===
create table if not exists infrx.provider_orgs (
  provider_org_id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  -- Display text: deliberately not unique and never referenced.
  display_name text not null check (length(btrim(display_name)) between 1 and 200),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now()
);

-- Provider roles are their own relation: distinct from `org_members.role` (consumer
-- owner/member) and from `profiles.is_operator` (the platform operator bit). A role change
-- is a revocation plus a new row; the only update is the one-way revocation (R59-8's
-- shape). No browser role can write here - a consumer owner cannot self-assign one.
create table if not exists infrx.provider_memberships (
  membership_id uuid primary key default gen_random_uuid(),
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  user_id uuid not null references public.profiles(id) on delete restrict,
  role text not null check (role in ('viewer', 'developer', 'administrator')),
  granted_by text not null check (length(btrim(granted_by)) between 1 and 200),
  granted_at timestamptz not null default infrx.now(),
  revoked_at timestamptz,
  constraint provider_memberships_revoked_after_granted
    check (revoked_at is null or revoked_at >= granted_at)
);
create unique index if not exists provider_memberships_one_current
  on infrx.provider_memberships (provider_org_id, user_id) where revoked_at is null;
create index if not exists provider_memberships_user_current
  on infrx.provider_memberships (user_id) where revoked_at is null;

create or replace function infrx.provider_memberships_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'membership % is never deleted; revoke it', old.membership_id
      using errcode = '23514';
  end if;
  if new.membership_id is distinct from old.membership_id
     or new.provider_org_id is distinct from old.provider_org_id
     or new.user_id is distinct from old.user_id or new.role is distinct from old.role
     or new.granted_by is distinct from old.granted_by
     or new.granted_at is distinct from old.granted_at
     or (old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at) then
    raise exception 'membership %: only a one-way revocation is permitted',
      old.membership_id using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger provider_memberships_guard before update or delete
  on infrx.provider_memberships for each row
  execute function infrx.provider_memberships_guard();

-- The provider_dev wallet's owner (0006 left the key to this file).
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'credit_wallets_provider_fk'
                 and conrelid = 'infrx.credit_wallets'::regclass) then
    alter table infrx.credit_wallets add constraint credit_wallets_provider_fk
      foreign key (owner_provider_org_id) references infrx.provider_orgs on delete restrict;
  end if;
end $$;

-- ============================================================ model identity ===
-- `public.models` gains the provider foreign key and a stable UUID identity (the v2
-- `model_id`). Versioned ownership is on the versions: every model/serving version
-- carries its provider, and the composite key below refuses re-owning a model that
-- already has versions (ON UPDATE RESTRICT) - a transfer is an explicit future process,
-- not an UPDATE. The public model id (`nemostation/marlin-2b`) stays the alias key.
alter table public.models
  add column if not exists model_uuid uuid not null default gen_random_uuid(),
  add column if not exists provider_org_id uuid;
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'models_model_uuid_key'
                 and conrelid = 'public.models'::regclass) then
    alter table public.models
      add constraint models_model_uuid_key unique (model_uuid),
      add constraint models_identity_key unique (id, model_uuid),
      add constraint models_owner_key unique (model_uuid, provider_org_id),
      add constraint models_provider_org_fk foreign key (provider_org_id)
        references infrx.provider_orgs on delete restrict;
  end if;
end $$;

-- ModelVersion: the immutable base artifact (07). Digest provenance is recorded (R76).
create table if not exists infrx.model_versions (
  model_version_id uuid primary key default gen_random_uuid(),
  model_id uuid not null,
  provider_org_id uuid not null,
  model_repo text not null check (length(btrim(model_repo)) between 1 and 200),
  model_commit text not null check (model_commit ~ '^[0-9a-f]{40}$'),
  -- One digest per shard, in shard order, at least one (06a: text[] chosen over a child
  -- relation; the whole array is the pinned identity and is never edited).
  weight_shard_digests text[] not null check (
    cardinality(weight_shard_digests) between 1 and 1024
    and array_ndims(weight_shard_digests) = 1
    and array_position(weight_shard_digests, null) is null
    and array_to_string(weight_shard_digests, ',')
        ~ '^sha256:[0-9a-f]{64}(,sha256:[0-9a-f]{64})*$'),
  adapter_digest text check (adapter_digest ~ '^sha256:[0-9a-f]{64}$'),
  tokenizer_digest text not null check (tokenizer_digest ~ '^sha256:[0-9a-f]{64}$'),
  chat_template_digest text not null check (chat_template_digest ~ '^sha256:[0-9a-f]{64}$'),
  digest_source text not null
    check (digest_source in ('served_bytes', 'registry_oid', 'registry_oid_confirmed')),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint model_versions_model_owner_fk foreign key (model_id, provider_org_id)
    references public.models (model_uuid, provider_org_id)
    on update restrict on delete restrict,
  constraint model_versions_identity_key unique (model_version_id, model_id, provider_org_id)
);

-- ServingVersion: everything that changes what the model does, on top of the version.
-- `runtime_image_digest` is NULL while the runtime is a moving tag (R76); pinning it is a
-- NEW serving version, because the row is immutable. The R62 string
-- `<public_model_id>@<revision_label>` maps to exactly one serving version per model.
create table if not exists infrx.serving_versions (
  serving_version_id uuid primary key default gen_random_uuid(),
  model_version_id uuid not null,
  model_id uuid not null,
  provider_org_id uuid not null,
  revision_label text not null check (revision_label ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
  prompt_harness_ref text not null check (length(btrim(prompt_harness_ref)) between 1 and 200),
  preprocessor_profile_version text not null
    check (length(btrim(preprocessor_profile_version)) between 1 and 200),
  runtime_image_ref text not null check (length(btrim(runtime_image_ref)) between 1 and 300),
  runtime_image_digest text check (runtime_image_digest ~ '^sha256:[0-9a-f]{64}$'),
  engine_options_digest text not null check (engine_options_digest ~ '^sha256:[0-9a-f]{64}$'),
  precision text not null check (length(btrim(precision)) between 1 and 50),
  -- Bounded, typed at the top level, one meter (06a: validated by the service against
  -- `CapabilityRecord`; the database refuses what can never be one).
  capability jsonb not null check (
    jsonb_typeof(capability) = 'object' and octet_length(capability::text) <= 4096
    and capability->>'billing_meter' = 'tokens-v1'
    and capability->>'api_family' = 'chat_completions'),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint serving_versions_version_fk foreign key (model_version_id, model_id, provider_org_id)
    references infrx.model_versions (model_version_id, model_id, provider_org_id)
    on delete restrict,
  constraint serving_versions_label_key unique (model_id, revision_label),
  constraint serving_versions_owner_key unique (serving_version_id, provider_org_id),
  constraint serving_versions_model_key unique (serving_version_id, model_id)
);

-- ============================================================ deployment ===
-- Endpoint: a stable dev/prod logical name of one provider. Dev/prod is a property of the
-- row, not a URL convention (07).
create table if not exists infrx.endpoints (
  endpoint_id uuid primary key default gen_random_uuid(),
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  name text not null check (name ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  environment text not null check (environment in ('dev', 'prod')),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint endpoints_name_key unique (provider_org_id, name, environment),
  constraint endpoints_owner_key unique (endpoint_id, provider_org_id, environment)
);

-- DeploymentRevision: an endpoint resolved to one serving version of the SAME provider,
-- with validated limits. Visibility is fixed at insert: a public revision is production
-- and in a public state, a private one never enters a public state (R70: a dev or
-- failed-validation revision can never be public). Promotion of a private revision is a
-- NEW public revision on a prod endpoint; only `state` moves, forward.
create table if not exists infrx.deployment_revisions (
  deployment_revision_id uuid primary key default gen_random_uuid(),
  endpoint_id uuid not null,
  provider_org_id uuid not null,
  environment text not null,
  serving_version_id uuid not null,
  visibility text not null check (visibility in ('private', 'public')),
  state text not null check (state in ('draft', 'validating', 'ready_private',
    'proposed_public', 'active', 'draining', 'retired')),
  max_input_tokens int not null check (max_input_tokens >= 1),
  max_output_tokens int not null check (max_output_tokens >= 1),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  constraint deployment_revisions_endpoint_fk
    foreign key (endpoint_id, provider_org_id, environment)
    references infrx.endpoints (endpoint_id, provider_org_id, environment) on delete restrict,
  constraint deployment_revisions_serving_fk foreign key (serving_version_id, provider_org_id)
    references infrx.serving_versions (serving_version_id, provider_org_id) on delete restrict,
  constraint deployment_revisions_visibility_matches_state check (case visibility
    when 'public' then environment = 'prod'
                       and state in ('proposed_public', 'active', 'draining', 'retired')
    else state in ('draft', 'validating', 'ready_private', 'retired') end),
  constraint deployment_revisions_serving_key unique (deployment_revision_id, serving_version_id),
  constraint deployment_revisions_visibility_key unique (deployment_revision_id, visibility)
);

create or replace function infrx.deployment_revisions_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'deployment revision % is never deleted; retire it',
      old.deployment_revision_id using errcode = '23514';
  end if;
  if new.deployment_revision_id is distinct from old.deployment_revision_id
     or new.endpoint_id is distinct from old.endpoint_id
     or new.provider_org_id is distinct from old.provider_org_id
     or new.environment is distinct from old.environment
     or new.serving_version_id is distinct from old.serving_version_id
     or new.visibility is distinct from old.visibility
     or new.max_input_tokens is distinct from old.max_input_tokens
     or new.max_output_tokens is distinct from old.max_output_tokens
     or new.created_by is distinct from old.created_by
     or new.created_at is distinct from old.created_at then
    raise exception 'deployment revision %: only its state moves',
      old.deployment_revision_id using errcode = '23514';
  end if;
  if new.state is distinct from old.state and not (
       (old.state = 'draft' and new.state in ('validating', 'retired'))
    or (old.state = 'validating' and new.state in ('ready_private', 'retired'))
    or (old.state = 'ready_private' and new.state = 'retired')
    or (old.state = 'proposed_public' and new.state in ('active', 'retired'))
    or (old.state = 'active' and new.state in ('draining', 'retired'))
    or (old.state = 'draining' and new.state = 'retired')) then
    raise exception 'deployment revision %: % -> % is not an allowed transition',
      old.deployment_revision_id, old.state, new.state using errcode = '23514';
  end if;
  new.updated_at := infrx.now();
  return new;
end $$;
create or replace trigger deployment_revisions_guard before update or delete
  on infrx.deployment_revisions for each row
  execute function infrx.deployment_revisions_guard();

-- ================================================================== rates ===
-- RateCardSnapshot (06a `rate_cards`): CREDIT, tokens-v1, per million, pinned to one
-- deployment revision AND its serving version, approved, both roundings named. A new rate
-- is a new row; the absence of any UPDATE path is "changing a rate affects newly admitted
-- requests, never prior holds". `provisional` labels a card whose price is not yet the
-- operator-approved launch price (P-01): it is admissible, and every job pinned to it is
-- identifiable afterwards.
create table if not exists infrx.rate_card_versions (
  rate_card_version text primary key check (rate_card_version ~ '^[a-z0-9][a-z0-9_.-]{0,99}$'),
  model_id uuid not null,
  deployment_revision_id uuid not null,
  serving_version_id uuid not null,
  unit text not null default 'CREDIT' check (unit = 'CREDIT'),
  meter text not null default 'tokens-v1' check (meter = 'tokens-v1'),
  input_rate_per_million numeric(20,8) not null check (input_rate_per_million >= 0),
  output_rate_per_million numeric(20,8) not null check (output_rate_per_million >= 0),
  effective_at timestamptz not null,
  status text not null default 'approved' check (status = 'approved'),
  approved_by text not null check (length(btrim(approved_by)) between 1 and 200),
  provisional boolean not null,
  hold_rounding text not null default 'ceiling_8' check (hold_rounding = 'ceiling_8'),
  debit_rounding text not null default 'half_up_8' check (debit_rounding = 'half_up_8'),
  created_at timestamptz not null default infrx.now(),
  constraint rate_card_versions_deployment_fk
    foreign key (deployment_revision_id, serving_version_id)
    references infrx.deployment_revisions (deployment_revision_id, serving_version_id)
    on delete restrict,
  constraint rate_card_versions_model_fk foreign key (serving_version_id, model_id)
    references infrx.serving_versions (serving_version_id, model_id) on delete restrict,
  constraint rate_card_versions_pins_key
    unique (rate_card_version, deployment_revision_id, serving_version_id, model_id)
);
create index if not exists rate_card_versions_deployment_idx
  on infrx.rate_card_versions (deployment_revision_id, effective_at desc);

-- The data-access policy identity a job pins (06a DataAccessPolicyRef).
create table if not exists infrx.data_access_policies (
  policy_version text primary key check (policy_version ~ '^[a-z0-9][a-z0-9_.-]{0,99}$'),
  effective_at timestamptz not null,
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now()
);

-- CatalogListing: an alias (`public.models.id`) pointed at an approved public production
-- deployment and the card that prices it. Alias movement and rollback are new rows with a
-- higher version; the listing a request resolves to is the highest effective version.
create table if not exists infrx.catalog_listings (
  public_model_id text not null,
  version int not null check (version >= 1),
  model_id uuid not null,
  deployment_revision_id uuid not null,
  serving_version_id uuid not null,
  rate_card_version text not null,
  visibility text not null default 'public' check (visibility = 'public'),
  effective_at timestamptz not null,
  approved_by text not null check (length(btrim(approved_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint catalog_listings_pkey primary key (public_model_id, version),
  constraint catalog_listings_model_fk foreign key (public_model_id, model_id)
    references public.models (id, model_uuid) on update restrict on delete restrict,
  constraint catalog_listings_card_fk
    foreign key (rate_card_version, deployment_revision_id, serving_version_id, model_id)
    references infrx.rate_card_versions
      (rate_card_version, deployment_revision_id, serving_version_id, model_id)
    on delete restrict,
  constraint catalog_listings_public_fk foreign key (deployment_revision_id, visibility)
    references infrx.deployment_revisions (deployment_revision_id, visibility)
    on delete restrict
);

-- Immutable after insert, and never truncated.
do $$
declare
  r text;
begin
  foreach r in array array['infrx.provider_orgs', 'infrx.model_versions',
                           'infrx.serving_versions', 'infrx.endpoints',
                           'infrx.rate_card_versions', 'infrx.data_access_policies',
                           'infrx.catalog_listings']
  loop
    execute format('create or replace trigger %I before update or delete on %s for each row '
                   'execute function infrx.forbid_update_delete()',
                   replace(r, 'infrx.', '') || '_immutable', r);
  end loop;
  foreach r in array array['infrx.provider_orgs', 'infrx.provider_memberships',
                           'infrx.model_versions', 'infrx.serving_versions', 'infrx.endpoints',
                           'infrx.deployment_revisions', 'infrx.rate_card_versions',
                           'infrx.data_access_policies', 'infrx.catalog_listings']
  loop
    execute format('create or replace trigger %I before truncate on %s for each statement '
                   'execute function infrx.forbid_truncate()',
                   replace(r, '.', '_') || '_no_truncate', r);
  end loop;
end $$;

-- ======================================================== jobs pin the registry ===
-- One composite key says the four pins are one approved card's own (model, deployment,
-- serving): a job cannot pin a card that prices another deployment or serving version
-- (R69). The policy pin names a recorded policy.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'jobs_pins_are_one_card'
                 and conrelid = 'infrx.jobs'::regclass) then
    alter table infrx.jobs
      add constraint jobs_pins_are_one_card
        foreign key (rate_card_version, deployment_revision_id, serving_version_id, model_id)
        references infrx.rate_card_versions
          (rate_card_version, deployment_revision_id, serving_version_id, model_id)
        on delete restrict,
      add constraint jobs_policy_version_fk foreign key (policy_version)
        references infrx.data_access_policies on delete restrict;
  end if;
end $$;

-- ================================================================ privileges ===
-- Browser roles: nothing (schema `infrx` stays closed). The platform role writes the
-- registry through operator setup tooling (G6B) - immutability and the state graph are
-- triggers, so an ALL grant cannot edit history - but never deletes or truncates.
do $$
declare
  r text;
begin
  foreach r in array array['infrx.provider_orgs', 'infrx.provider_memberships',
                           'infrx.model_versions', 'infrx.serving_versions', 'infrx.endpoints',
                           'infrx.deployment_revisions', 'infrx.rate_card_versions',
                           'infrx.data_access_policies', 'infrx.catalog_listings']
  loop
    execute format('alter table %s enable row level security', r);
    execute format('revoke all on %s from public, anon, authenticated, service_role', r);
    execute format('grant select, insert on %s to service_role', r);
  end loop;
end $$;
grant update (revoked_at) on infrx.provider_memberships to service_role;
grant update (state, updated_at) on infrx.deployment_revisions to service_role;
revoke all on function infrx.provider_memberships_guard() from public, anon, authenticated;
revoke all on function infrx.deployment_revisions_guard() from public, anon, authenticated;
