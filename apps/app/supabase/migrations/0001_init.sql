-- Console schema: profiles, orgs, models, api keys, usage, credits.
-- Spec: apps/README.md §6. RLS on every table; the gateway writes with the
-- service role (which bypasses RLS), the console reads with the user's JWT.

-- ---------------------------------------------------------------- tables ---

create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  email text not null,
  full_name text,
  avatar_url text,
  is_operator boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  created_by uuid references public.profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.org_members (
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  role text not null check (role in ('owner','member')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index org_members_user_id_idx on public.org_members (user_id);

create table public.models (
  id text primary key,                          -- 'nemostation/marlin-2b'
  name text not null,
  provider text not null,
  description text not null,
  status text not null check (status in ('live','coming_soon','retired')),
  base_url text not null,                       -- https://marlin2b.callbill.ai/v1
  served_model text not null,                   -- id to send in the request body
  input_usd_per_m numeric(12,6) not null,
  output_usd_per_m numeric(12,6) not null,
  cache_usd_per_m numeric(12,6),
  context_tokens int not null,
  max_output_tokens int,
  input_modalities text[] not null,
  output_modalities text[] not null,
  limits jsonb not null default '{}',           -- {max_video_seconds:120, ...}
  snippets jsonb not null default '{}',         -- {curl,python,javascript} with {{KEY}} {{BASE_URL}}
  sort int not null default 100,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index models_status_sort_idx on public.models (status, sort);

create table public.api_keys (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  created_by uuid references public.profiles(id),
  name text not null,
  prefix text not null,                         -- 'sk-infrx-' + 8 chars, shown in the UI
  key_hash text unique not null,                -- sha256 hex of the full key
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);
create index api_keys_org_id_idx on public.api_keys (org_id, created_at desc);

create table public.usage_events (
  id uuid primary key,                          -- the gateway's Inference-Id
  org_id uuid not null references public.organizations(id) on delete cascade,
  api_key_id uuid references public.api_keys(id) on delete set null,
  model_id text not null references public.models(id),
  status int not null,
  stream boolean not null default false,
  prompt_tokens int,
  completion_tokens int,
  video_seconds numeric(10,3),
  ttft_ms int,
  latency_ms int,
  cached boolean not null default false,
  cost_usd numeric(14,8) not null default 0,
  created_at timestamptz not null default now()
);
create index usage_events_org_created_idx on public.usage_events (org_id, created_at desc);
create index usage_events_key_created_idx on public.usage_events (api_key_id, created_at desc);

create table public.credit_ledger (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  delta_usd numeric(14,6) not null,
  kind text not null check (kind in ('grant','purchase','usage','adjustment')),
  reason text,
  ref text,
  created_by uuid references public.profiles(id),
  created_at timestamptz not null default now()
);
create index credit_ledger_org_created_idx on public.credit_ledger (org_id, created_at desc);

-- ----------------------------------------------------------- updated_at ---

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();
create trigger organizations_set_updated_at before update on public.organizations
  for each row execute function public.set_updated_at();
create trigger models_set_updated_at before update on public.models
  for each row execute function public.set_updated_at();

-- -------------------------------------------------------- rls helpers ---
-- security definer so policies on org_members can call them without recursing
-- into the very policy being evaluated.

create or replace function public.is_operator()
returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce((select p.is_operator from public.profiles p where p.id = auth.uid()), false);
$$;

create or replace function public.is_org_member(org uuid)
returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (
    select 1 from public.org_members m
    where m.org_id = org and m.user_id = auth.uid());
$$;

create or replace function public.is_org_owner(org uuid)
returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (
    select 1 from public.org_members m
    where m.org_id = org and m.user_id = auth.uid() and m.role = 'owner');
$$;

grant execute on function public.is_operator() to authenticated;
grant execute on function public.is_org_member(uuid) to authenticated;
grant execute on function public.is_org_owner(uuid) to authenticated;

-- ------------------------------------------------------------- new user ---
-- One profile + one personal organization + owner membership per auth user.

create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_org uuid;
  v_name text;
begin
  insert into public.profiles (id, email, full_name, avatar_url)
  values (
    new.id,
    coalesce(new.email, new.id::text),
    nullif(new.raw_user_meta_data->>'full_name', ''),
    nullif(new.raw_user_meta_data->>'avatar_url', ''))
  on conflict (id) do nothing;

  if not exists (select 1 from public.org_members m where m.user_id = new.id) then
    v_name := split_part(coalesce(new.email, 'workspace'), '@', 1);
    insert into public.organizations (name, slug, created_by)
    values (v_name, substr(replace(gen_random_uuid()::text, '-', ''), 1, 12), new.id)
    returning id into v_org;

    insert into public.org_members (org_id, user_id, role) values (v_org, new.id, 'owner');
  end if;

  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ------------------------------------------------------------- policies ---

alter table public.profiles       enable row level security;
alter table public.organizations  enable row level security;
alter table public.org_members    enable row level security;
alter table public.models         enable row level security;
alter table public.api_keys       enable row level security;
alter table public.usage_events   enable row level security;
alter table public.credit_ledger  enable row level security;

-- profiles: yourself, anyone sharing an org with you, everyone if operator.
create policy profiles_select on public.profiles for select to authenticated
  using (
    id = auth.uid()
    or public.is_operator()
    or exists (
      select 1 from public.org_members mine
      join public.org_members theirs on theirs.org_id = mine.org_id
      where mine.user_id = auth.uid() and theirs.user_id = profiles.id));

create policy profiles_update_self on public.profiles for update to authenticated
  using (id = auth.uid()) with check (id = auth.uid());

-- RLS cannot restrict columns: column grants keep a user from promoting
-- themselves to operator through profiles_update_self.
revoke insert, update, delete on public.profiles from anon, authenticated;
grant update (full_name, avatar_url) on public.profiles to authenticated;

-- organizations
create policy organizations_select on public.organizations for select to authenticated
  using (public.is_org_member(id) or public.is_operator());

create policy organizations_update_owner on public.organizations for update to authenticated
  using (public.is_org_owner(id) or public.is_operator())
  with check (public.is_org_owner(id) or public.is_operator());

revoke insert, delete on public.organizations from anon, authenticated;

-- org_members: read only; membership is created by the signup trigger.
create policy org_members_select on public.org_members for select to authenticated
  using (public.is_org_member(org_id) or public.is_operator());

revoke insert, update, delete on public.org_members from anon, authenticated;

-- models: catalog, readable by any signed-in user; written by the service role.
create policy models_select on public.models for select to authenticated
  using (true);

revoke insert, update, delete on public.models from anon, authenticated;

-- api_keys: members read, owners (and operators) create and revoke.
create policy api_keys_select on public.api_keys for select to authenticated
  using (public.is_org_member(org_id) or public.is_operator());

create policy api_keys_insert_owner on public.api_keys for insert to authenticated
  with check (public.is_org_owner(org_id) and created_by = auth.uid());

create policy api_keys_update_owner on public.api_keys for update to authenticated
  using (public.is_org_owner(org_id) or public.is_operator())
  with check (public.is_org_owner(org_id) or public.is_operator());

revoke delete on public.api_keys from anon, authenticated;

-- usage_events / credit_ledger: read-only for members and operators.
-- No insert/update/delete policy at all: writes are service-role only.
create policy usage_events_select on public.usage_events for select to authenticated
  using (public.is_org_member(org_id) or public.is_operator());

create policy credit_ledger_select on public.credit_ledger for select to authenticated
  using (public.is_org_member(org_id) or public.is_operator());

revoke insert, update, delete on public.usage_events, public.credit_ledger
  from anon, authenticated;

-- ------------------------------------------------------------ reporting ---
-- security invoker: RLS on usage_events still applies, the explicit guard just
-- turns "empty result" into a clear error.

create or replace function public.org_usage_summary(
  p_org uuid,
  p_from timestamptz,
  p_to timestamptz,
  p_key uuid default null)
returns table (
  requests bigint,
  error_requests bigint,
  avg_rps numeric,
  ttft_p50_ms numeric,
  tok_s_p50 numeric,
  latency_p50_ms numeric,
  cache_hit_ratio numeric,
  prompt_tokens bigint,
  completion_tokens bigint,
  cost_usd numeric)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
begin
  if not (public.is_org_member(p_org) or public.is_operator()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;

  return query
  select
    count(*)::bigint,
    count(*) filter (where e.status >= 400)::bigint,
    round(count(*)::numeric / greatest(extract(epoch from (p_to - p_from)), 1)::numeric, 4),
    round((percentile_cont(0.5) within group (order by e.ttft_ms::double precision)
             filter (where e.ttft_ms is not null))::numeric, 1),
    round((percentile_cont(0.5) within group (
             order by e.completion_tokens::double precision
                      / (e.latency_ms - e.ttft_ms) * 1000)
             filter (where e.ttft_ms is not null
                     and e.latency_ms > e.ttft_ms
                     and coalesce(e.completion_tokens, 0) > 0))::numeric, 2),
    round((percentile_cont(0.5) within group (order by e.latency_ms::double precision)
             filter (where e.latency_ms is not null))::numeric, 1),
    round(count(*) filter (where e.cached)::numeric / nullif(count(*), 0), 4),
    coalesce(sum(e.prompt_tokens), 0)::bigint,
    coalesce(sum(e.completion_tokens), 0)::bigint,
    coalesce(sum(e.cost_usd), 0)::numeric
  from public.usage_events e
  where e.org_id = p_org
    and e.created_at >= p_from
    and e.created_at < p_to
    and (p_key is null or e.api_key_id = p_key);
end $$;

create or replace function public.org_usage_daily(
  p_org uuid,
  p_from timestamptz,
  p_to timestamptz,
  p_key uuid default null)
returns table (
  day date,
  requests bigint,
  prompt_tokens bigint,
  completion_tokens bigint,
  cost_usd numeric)
language plpgsql stable security invoker set search_path = public, pg_temp as $$
begin
  if not (public.is_org_member(p_org) or public.is_operator()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;

  return query
  select
    (e.created_at at time zone 'utc')::date,
    count(*)::bigint,
    coalesce(sum(e.prompt_tokens), 0)::bigint,
    coalesce(sum(e.completion_tokens), 0)::bigint,
    coalesce(sum(e.cost_usd), 0)::numeric
  from public.usage_events e
  where e.org_id = p_org
    and e.created_at >= p_from
    and e.created_at < p_to
    and (p_key is null or e.api_key_id = p_key)
  group by 1
  order by 1;
end $$;

create or replace function public.org_balance(p_org uuid)
returns numeric
language plpgsql stable security invoker set search_path = public, pg_temp as $$
declare
  v numeric;
begin
  if not (public.is_org_member(p_org) or public.is_operator()) then
    raise exception 'not a member of organization %', p_org using errcode = '42501';
  end if;

  select coalesce(sum(l.delta_usd), 0) into v
  from public.credit_ledger l where l.org_id = p_org;
  return v;
end $$;

grant execute on function public.org_usage_summary(uuid, timestamptz, timestamptz, uuid) to authenticated;
grant execute on function public.org_usage_daily(uuid, timestamptz, timestamptz, uuid) to authenticated;
grant execute on function public.org_balance(uuid) to authenticated;
