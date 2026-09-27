-- LW1 integration (Lab, local-only, R151): the Lab App's session door to THE one membership
-- read (R156, WR-L1-5/WR-L1-7). Strictly additive; no table, row or existing grant changes.
--
--   public.lab_provider_memberships()   the signed-in user's CURRENT provider workspaces,
--                                       [{provider_org_id, provider_name, role}], read from
--                                       0027's infrx.lab_provider_memberships for auth.uid()
--                                       only. Current = ProviderMembership.is_current on the
--                                       database clock (R7): granted_at <= infrx.now() <
--                                       revoked_at. No argument, so no caller names an
--                                       identity; no other column (user, granting operator,
--                                       dates) leaves the database. PostgREST exposes
--                                       `public` only, never `infrx` (supabase/README.md).
--
-- EXECUTE: authenticated only (0004's default privileges already withhold it; revoked again
-- here explicitly). The Lab holds no service-role key.
--
-- ROLLBACK (this file alone; nothing references it):
--   drop function public.lab_provider_memberships();

create or replace function public.lab_provider_memberships() returns jsonb
language sql stable security definer set search_path = public, infrx, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('provider_org_id', r->'provider_org_id',
      'provider_name', r->'provider_name', 'role', r->'role') order by n), '[]')
    from jsonb_array_elements(infrx.lab_provider_memberships(
           jsonb_build_object('user_id', auth.uid()))) with ordinality as m(r, n)
   where (r->>'granted_at')::timestamptz <= infrx.now()
     and (r->>'revoked_at' is null or infrx.now() < (r->>'revoked_at')::timestamptz)
$$;

revoke all on function public.lab_provider_memberships() from public, anon;
grant execute on function public.lab_provider_memberships() to authenticated;
