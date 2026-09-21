-- TEST FIXTURE, NEVER A MIGRATION.
--
-- `apps/app/supabase/migrations/0001_init.sql` references `auth.users`,
-- `auth.uid()` and the roles `anon`, `authenticated`, `service_role`, which are
-- Supabase's and not PostgreSQL's, so the migration set does not apply to a plain
-- `postgres` image. This file is the minimal shim that makes a task-local database
-- resemble the deployed one. It lives here, and deliberately NOT in
-- `apps/app/supabase/migrations/`, because the Supabase CLI applies that directory
-- and production already has everything below.
--
-- Why a shim rather than a pinned `supabase/postgres` image: the two things the
-- migrations actually depend on are (1) those three roles with Supabase's default
-- privileges and (2) `auth.uid()` reading the PostgREST request GUC. Both are a
-- dozen lines. The Supabase image is a multi-gigabyte pull that this host does not
-- have and that would still need the same GUC set by hand to exercise RLS.
--
-- The part that matters for D1's role matrix is the DEFAULT PRIVILEGES block:
-- Supabase grants `anon`, `authenticated` and `service_role` ALL PRIVILEGES on
-- everything created in `public`, which is exactly why a new protected column on an
-- existing table is writable by a browser role unless a migration revokes it
-- (02: "RLS alone is insufficient when broad existing update grants cover new
-- protected fields"). A shim without it would make the attack matrix pass
-- vacuously.
--
-- Known differences from Supabase, none of which the migrations touch: no
-- `auth.jwt()`, no `pgjwt`/`pgsodium`/`realtime`/`storage` schemas, no
-- `authenticator` login role, `auth.users` carries only the three columns
-- `0001_init.sql` reads, and RLS is exercised by setting `role` +
-- `request.jwt.claims` the way PostgREST does rather than by presenting a JWT.

create extension if not exists pgcrypto;

-- ------------------------------------------------------------------- roles ---
-- `nologin`: the tests reach them with SET ROLE, as PostgREST does.
-- `bypassrls` on service_role mirrors Supabase; it is what makes "the gateway
-- writes with the service role (which bypasses RLS)" true in 0001's header.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end $$;

-- -------------------------------------------------------------- auth schema ---
create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text,
  raw_user_meta_data jsonb not null default '{}',
  created_at timestamptz not null default now()
);

-- Supabase's definition: the subject claim of the verified JWT PostgREST puts in the
-- `request.jwt.claims` GUC. Two details copied from it on purpose: `true` makes a
-- missing GUC null rather than an error, and the `nullif` runs BEFORE the `::jsonb`
-- cast, because an unset GUC reads as the empty string and `''::jsonb` raises - which
-- turns every anonymous query into a 500 instead of a denial.
create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub',
                '')::uuid;
$$;

create or replace function auth.role() returns text
language sql stable as $$
  select nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role',
                '');
$$;

-- --------------------------------------------------- Supabase grant defaults ---
grant usage on schema public to anon, authenticated, service_role;
grant usage on schema auth to anon, authenticated, service_role;

alter default privileges in schema public
  grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public
  grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public
  grant all on sequences to anon, authenticated, service_role;

grant all on all tables in schema public to anon, authenticated, service_role;
grant all on all routines in schema public to anon, authenticated, service_role;
grant all on all sequences in schema public to anon, authenticated, service_role;
