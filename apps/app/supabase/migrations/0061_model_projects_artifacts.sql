-- AP-04 (wave 7, lane api-artifacts; R271 allocates 0061): model projects and verified
-- artifacts (research/plan/api-lifecycle/contracts.md §4). The registry stays 0007's: a
-- serving revision of a project IS an `infrx.serving_versions` row written by A3's
-- `PgRegistry` through `LabControl.register`; this file only adds the project, the immutable
-- artifact manifest, the two intake sessions (upload, pinned import) and the link from a
-- serving version to the verified artifact it was built from.
-- LOCAL-ONLY (R151/R201/R271): never applied hosted; applied only in an R151/R269 window.
--
--   model_projects            provider-scoped slug; `model_uuid`/`public_model_id` stay NULL
--                             until the first serving revision binds the project to a
--                             `public.models` row (`model_project_bind`): creating a project
--                             makes no catalog or serving claim.
--   artifacts                 immutable: files[] {relative_path, bytes, sha256, media_type},
--                             source upload | import | adopted, source commit, provenance,
--                             the compatibility report and the verification instant.
--   artifact_uploads          one bounded upload session: declared manifest, expiry, state
--                             open -> verifying -> verified | failed, open -> expired; the
--                             paths found at completion (`received`).
--   artifact_imports          one pinned import: allowlisted host, repository, 40-hex commit,
--                             an optional secret REFERENCE (never a token), state.
--   model_project_revisions   serving_version_id -> (project, verified artifact, requested
--                             profile): the link 0007's immutable serving rows lack.
--
-- Writers: the Lab control login `infrx_lab_control` (0043) through `PgArtifactStore`
-- (`infrx/lab/artifacts/store.py`) and the platform role; browser roles reach nothing.
-- `request_hash` is the canonical input hash of the idempotent create (R270).
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.model_project_bind(jsonb), infrx.model_projects_guard(); drop table
--   infrx.model_project_revisions, infrx.artifact_imports, infrx.artifact_uploads,
--   infrx.artifacts, infrx.model_projects. `public.models` rows a bind wrote stay (serving
--   versions reference them).
--
-- Re-runnable: `if not exists`, `create or replace`, `drop policy if exists`.

create table if not exists infrx.model_projects (
  project_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  name text not null check (length(btrim(name)) between 1 and 200),
  description text not null check (length(description) <= 4000),
  model_uuid uuid,
  public_model_id text,
  request_hash text not null check (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint model_projects_slug_key unique (provider_org_id, slug),
  constraint model_projects_model_key unique (model_uuid),
  constraint model_projects_owner_key unique (project_id, provider_org_id),
  constraint model_projects_model_fk foreign key (model_uuid, provider_org_id)
    references public.models (model_uuid, provider_org_id) on delete restrict,
  constraint model_projects_bound_together
    check ((model_uuid is null) = (public_model_id is null))
);

-- A project's identity never changes; the one update is the bind (NULL -> a model, once).
create or replace function infrx.model_projects_guard() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'model project % is never deleted', old.project_id using errcode = '23514';
  end if;
  if old.model_uuid is not null
     or (to_jsonb(new) - 'model_uuid' - 'public_model_id')
        is distinct from (to_jsonb(old) - 'model_uuid' - 'public_model_id') then
    raise exception 'model project %: only a first bind is permitted', old.project_id
      using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger model_projects_guard before update or delete
  on infrx.model_projects for each row execute function infrx.model_projects_guard();

create table if not exists infrx.artifacts (
  artifact_id uuid primary key,
  project_id uuid not null,
  provider_org_id uuid not null,
  source text not null check (source in ('upload', 'import', 'adopted')),
  source_repo text check (length(btrim(source_repo)) between 1 and 200),
  source_commit text check (source_commit ~ '^[0-9a-f]{40}$'),
  files jsonb not null check (jsonb_typeof(files) = 'array'
    and jsonb_array_length(files) between 1 and 256),
  manifest_sha256 text not null check (manifest_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  card jsonb not null check (jsonb_typeof(card) = 'object'
    and octet_length(card::text) <= 16384),
  provenance jsonb not null check (jsonb_typeof(provenance) = 'object'),
  compatibility jsonb not null check (jsonb_typeof(compatibility) = 'object'),
  verified_at timestamptz not null,
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint artifacts_project_fk foreign key (project_id, provider_org_id)
    references infrx.model_projects (project_id, provider_org_id) on delete restrict,
  constraint artifacts_owner_key unique (artifact_id, provider_org_id),
  -- an import is pinned to its source commit; an upload has none
  constraint artifacts_source_pinned check (source <> 'import'
    or (source_repo is not null and source_commit is not null))
);
create or replace trigger artifacts_immutable before update or delete
  on infrx.artifacts for each row execute function infrx.forbid_update_delete();

create table if not exists infrx.artifact_uploads (
  upload_id uuid primary key,
  project_id uuid not null,
  provider_org_id uuid not null,
  files jsonb not null check (jsonb_typeof(files) = 'array'
    and jsonb_array_length(files) between 1 and 256),
  manifest_sha256 text not null check (manifest_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  card jsonb not null check (jsonb_typeof(card) = 'object'
    and octet_length(card::text) <= 16384),
  state text not null check (state in ('open', 'verifying', 'verified', 'failed', 'expired')),
  expires_at timestamptz not null,
  received jsonb not null default '[]' check (jsonb_typeof(received) = 'array'),
  operation_id uuid,
  artifact_id uuid,
  request_hash text not null check (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  constraint artifact_uploads_project_fk foreign key (project_id, provider_org_id)
    references infrx.model_projects (project_id, provider_org_id) on delete restrict,
  constraint artifact_uploads_artifact_fk foreign key (artifact_id, provider_org_id)
    references infrx.artifacts (artifact_id, provider_org_id) on delete restrict,
  constraint artifact_uploads_verified_has_artifact
    check ((state = 'verified') = (artifact_id is not null)),
  constraint artifact_uploads_verifying_has_operation
    check (state in ('open', 'expired') or operation_id is not null)
);
create index if not exists artifact_uploads_pending on infrx.artifact_uploads (state, expires_at)
  where state in ('open', 'verifying');

create table if not exists infrx.artifact_imports (
  import_id uuid primary key,
  project_id uuid not null,
  provider_org_id uuid not null,
  source_host text not null check (source_host in ('huggingface.co')),
  source_repo text not null check (source_repo ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$'),
  source_commit text not null check (source_commit ~ '^[0-9a-f]{40}$'),
  -- a reference the worker resolves (env:NAME | ssm:/path), never a credential
  secret_ref text check (secret_ref ~ '^(env:[A-Z][A-Z0-9_]{0,63}|ssm:/[A-Za-z0-9_./-]{1,200})$'),
  files jsonb not null check (jsonb_typeof(files) = 'array'
    and jsonb_array_length(files) between 1 and 256),
  manifest_sha256 text not null check (manifest_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  card jsonb not null check (jsonb_typeof(card) = 'object'
    and octet_length(card::text) <= 16384),
  state text not null check (state in ('queued', 'verified', 'failed')),
  operation_id uuid not null,
  artifact_id uuid,
  request_hash text not null check (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  constraint artifact_imports_project_fk foreign key (project_id, provider_org_id)
    references infrx.model_projects (project_id, provider_org_id) on delete restrict,
  constraint artifact_imports_artifact_fk foreign key (artifact_id, provider_org_id)
    references infrx.artifacts (artifact_id, provider_org_id) on delete restrict,
  constraint artifact_imports_verified_has_artifact
    check ((state = 'verified') = (artifact_id is not null))
);
create index if not exists artifact_imports_pending on infrx.artifact_imports (state)
  where state = 'queued';

create table if not exists infrx.model_project_revisions (
  serving_version_id uuid primary key,
  project_id uuid not null,
  provider_org_id uuid not null,
  artifact_id uuid not null,
  profile jsonb not null check (jsonb_typeof(profile) = 'object'),
  request_hash text not null check (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint model_project_revisions_project_fk foreign key (project_id, provider_org_id)
    references infrx.model_projects (project_id, provider_org_id) on delete restrict,
  constraint model_project_revisions_artifact_fk foreign key (artifact_id, provider_org_id)
    references infrx.artifacts (artifact_id, provider_org_id) on delete restrict
);
create or replace trigger model_project_revisions_immutable before update or delete
  on infrx.model_project_revisions for each row execute function infrx.forbid_update_delete();

-- The first serving revision binds the project to a `public.models` row (0007's model
-- identity; the registry's foreign keys need it): `<provider slug>/<project slug>`, status
-- `coming_soon`, listed nowhere until an operator publishes. An adopted project arrives bound.
-- SECURITY DEFINER: the control login gains no grant on `public.models`.
create or replace function infrx.model_project_bind(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  p infrx.model_projects%rowtype;
  v_public text;
  v_model uuid;
begin
  select * into p from infrx.model_projects
   where project_id = (p_args->>'project_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such model project for this provider');
  end if;
  if p.model_uuid is null then
    select o.slug || '/' || p.slug into v_public from infrx.provider_orgs o
     where o.provider_org_id = p.provider_org_id;
    insert into public.models (id, name, provider, description, status, base_url,
      served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities,
      output_modalities, provider_org_id)
    select v_public, p.name, o.display_name, '', 'coming_soon', '', p.slug, 0, 0, 0,
           '{}', '{}', p.provider_org_id
      from infrx.provider_orgs o where o.provider_org_id = p.provider_org_id
    on conflict (id) do nothing
    returning model_uuid into v_model;
    if v_model is null then
      perform infrx.refuse('state_conflict', 'the public model id ' || v_public
                           || ' is already another model''s');
    end if;
    update infrx.model_projects set model_uuid = v_model, public_model_id = v_public
     where project_id = p.project_id returning * into p;
  end if;
  return jsonb_build_object('model_uuid', p.model_uuid, 'public_model_id', p.public_model_id);
end $$;

-- ===================================================================== privileges ===
do $$
declare
  r text;
begin
  foreach r in array array['model_projects', 'artifacts', 'artifact_uploads',
                           'artifact_imports', 'model_project_revisions'] loop
    execute format('alter table infrx.%I enable row level security', r);
    execute format('revoke all on infrx.%I from public, anon, authenticated', r);
    execute format('drop policy if exists api_artifacts_control on infrx.%I', r);
    execute format('create policy api_artifacts_control on infrx.%I to infrx_lab_control '
                   'using (true) with check (true)', r);
  end loop;
end $$;
grant select, insert, update on infrx.artifact_uploads, infrx.artifact_imports
  to infrx_lab_control;
grant select, insert on infrx.model_projects, infrx.artifacts, infrx.model_project_revisions
  to infrx_lab_control;
revoke all on function infrx.model_project_bind(jsonb) from public, anon, authenticated;
grant execute on function infrx.model_project_bind(jsonb) to infrx_lab_control;
