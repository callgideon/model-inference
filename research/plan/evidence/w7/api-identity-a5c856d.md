# api-identity (AP-01, slices 01a-01d) - lane evidence at a5c856d

Lane api-identity, wave 7 (LW7), branch `codex/w7-api-identity`, worktree
`.claude/worktrees/codex-w7-api-identity`, base `cd9f517c` (the keystone), code head `a5c856d5`.
Task-local key `ap1` (PostgreSQL 57551); no other port, container or database used. No hosted
Supabase, box, AWS, Vercel or secret was touched; GoTrue is the local stub throughout.

## Changed paths (all new, all owned)

- `apps/infrx-api/infrx/auth_facade/__init__.py` - `AuthFacade` (GoTrue REST: password grant,
  signup with `gotrue_meta_security.captcha_token`, recover, refresh, logout, `PUT /user`, PKCE
  exchange, email-link verify, settings), flow.ts's failure table (`BY_CODE`, `failure`,
  `safe_next`, `MESSAGES` = `FAILURE_COPY`), `AuthRefused` rendering.
- `apps/infrx-api/infrx/auth_facade/stub.py` - the local GoTrue stand-in (ASGI app; error shape
  `{code, error_code, msg}`); never composed.
- `apps/infrx-api/infrx/console/__init__.py` - `EnvelopeRoute` (R270 envelope for FastAPI-declared
  bodies: validation failures as `invalid_request` + field names/type codes, never `{detail}` or
  input values), `idempotency_key`.
- `apps/infrx-api/infrx/console/session.py` - `SessionActors` (`control.ActorSource`), `web_session`,
  `PgIdentity` (account summary, members, grant/revoke, provider creation) + records.
- `apps/infrx-api/infrx/gateway/routes/auth.py` - `/auth/v1/sign-in|sign-up|refresh|sign-out|recovery|password`
  (POST), `/auth/v1/callback`, `/auth/v1/availability` (GET).
- `apps/infrx-api/infrx/gateway/routes/console_me.py` - `GET /console/v1/me`, `GET /console/v1/capabilities`.
- `apps/infrx-api/infrx/gateway/routes/lab_workspaces.py` - `GET /lab/v1/workspaces`,
  `GET /lab/v1/capabilities?provider_org_id=`, `GET|POST /lab/v1/workspaces/{id}/members`,
  `DELETE /lab/v1/workspaces/{id}/members/{user}`.
- `apps/infrx-api/infrx/gateway/routes/operator_providers.py` - `POST /operator/v1/providers`.
- `apps/infrx-api/tests/ap01/` - `test_auth.py` (19 cases), `test_identity.py` (14 cases: 10 run in
  both worlds, 3 in memory only, 1 PostgreSQL only), `worlds.py`, `conftest.py`, `mutants.py`,
  `test_mutants.py`.
- `research/plan/evidence/w7/api-identity-a5c856d-WR-AP01-1.patch` - the wiring request's exact patch.

## Commands (exit codes and counts)

| Command (from `apps/infrx-api` unless noted) | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap01/test_auth.py` (01a red, 23:49Z) | 2 | collection ImportError: `FAILURES` absent from `infrx.auth_facade` |
| `uv run --frozen pytest -q tests/ap01/test_identity.py` (01b-01d red, 23:58Z) | 2 | collection ImportError: `console_me` absent from `infrx.gateway.routes` |
| `INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_auth.py tests/ap01/test_identity.py` | 0 | 43 passed (19 auth + 13 in memory + 11 PostgreSQL) |
| `INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_identity.py -m pg` | 0 | 11 passed on `supabase/postgres@sha256:7768d0d1...` (no shim), service_role set by `connector` |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_mutants.py` | 0 | 67 passed = 49 memory mutants + 12 PostgreSQL mutants killed, 0 survivors; well-formed, every-case, list-separation and 3 runner self-tests green |
| `make api-lint` (repo root) | 0 | All checks passed (ruff 0.15.12, no per-file ignore added) |
| `uvx pyright@1.1.414 -p pyproject.toml --outputjson` | - | 458 errors = baseline 458; 0 in the lane's files |
| `python -m pytest -q tests/integration/test_makefile_mutant_lists.py` (repo root, this branch) | 1 | 7 failed: `tests/ap01/test_mutants.py` named 0 times - closed by WR-AP01-1's Makefile line (7 passed with the patch applied) |
| WR-AP01-1 applied in a scratch detached worktree: `INFRX_D_TASK=ap1 python -m pytest -q tests/g tests/i tests/ap01 tests/contracts` (mutant lists deselected) | 1 then 0 | first run 5 failed / 2770 passed (the pins the patch then updates: ROUTERS literal x2, frozen deployment names, preflight schema, composed test vs. pre-a5c856d routes); after adding those pins: the failing files plus composed/startup/packaging rerun 482 passed |

Failed-then-passed regressions: both red runs above (the seam tests recorded before the
implementation); `test_mutants` first full run reported `rate_limit_without_retry_after` and
`validation_left_to_fastapi` as broken_runner (KeyError deaths in the test, not assertions) - the
assertions were rewritten to compare values (`.get`, status/code tuples), then 0 survivors.

## API-IDENTITY failure matrix (where each oracle lives)

expired/signed-out token -> 401 and IdP outage -> 503 (`a_key_or_a_dead_session_is_not_a_web_session`,
`an_unreachable_or_failing_idp_is_503_never_bad_credentials`); forged provider -> 404
(`lab_capabilities_are_the_members_own`, `members_are_read_by_members_only`); wrong audience (consumer key
at every web door) -> 401, at the operator door -> 403 (`only_an_operator_creates_providers`); revoked
member refused on the next call (`a_revoked_member_is_refused_at_once`, both worlds); developer cannot
self-promote / admin cannot change own membership (`only_an_administrator_changes_members_and_never_their_own`);
cross-origin submission -> 403 before the IdP or the store (`a_cross_origin_submission_never_reaches_the_idp`,
`member_mutations_need_an_idempotency_key_and_the_origin`); enumeration policy as flow.ts
(`a_wrong_password_and_an_unknown_email_read_the_same`, `sign_up_of_an_existing_email_reads_sent`,
`recovery_of_an_unknown_email_reads_sent`); no password/token in logs or errors
(`no_password_or_token_reaches_a_log_or_an_error`); NemoStation never reassigned
(`test_identity_pg__an_existing_provider_is_never_reassigned`).

## WIRING REQUEST WR-AP01-1 (coordinator; exact patch in `api-identity-a5c856d-WR-AP01-1.patch`)

1. `infrx/config.py` `DeploymentSettings`: `identity_api: bool = False`, `web_origins: str = ""`,
   `auth_facade: bool = False`, `supabase_anon_key: str = field(default="", repr=False)`,
   `auth_captcha_required: bool = False` (env `IDENTITY_API`, `WEB_ORIGINS`, `AUTH_FACADE`,
   `SUPABASE_ANON_KEY`, `AUTH_CAPTCHA_REQUIRED`; all default OFF/empty).
2. `infrx/gateway/pilot.py`: `adapters_from_env` adds `**_identity(settings, connect)` (`PgIdentity` +
   `LabAccess(PgAccessStore)` on the pool, only with IDENTITY_API); `build_ingress_deps(...,
   identity=None, lab_access=None)` sets `rt.identity`, `rt.lab_access`, `rt.actors =
   SessionActors(GoTrueSessions(SUPABASE_URL, service key), identity, keys=AuthResolver(rt),
   origins=WEB_ORIGINS)` when IDENTITY_API (missing store -> RuntimeMisconfigured) and
   `rt.auth_facade = AuthFacade(SUPABASE_URL, SUPABASE_ANON_KEY, ...)` when AUTH_FACADE (no anon key ->
   RuntimeMisconfigured naming it); each None otherwise.
3. `infrx/gateway/app.py` `ROUTERS`: `auth, console_me, lab_workspaces, operator_providers` after
   `lab_checkpoints`, before `metrics`.
4. `Makefile` `api-mutants`: `cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_mutants.py`.
5. Pins the change moves: `tests/g/test_startup.py` (ROUTERS tuple), `tests/contracts/test_config_and_imports.py`
   (router names, five frozen deployment names), `deploy/preflight.py` `NOT_SETTABLE` (five names, a deploy
   change, never `--set`).
6. Composed test `tests/ap01/test_composed.py` (in the patch): off by default nothing mounts; IDENTITY_API
   composes `SessionActors` (key door `AuthResolver`, parsed origins) and mounts the identity routes;
   IDENTITY_API without the store refuses; AUTH_FACADE needs `SUPABASE_ANON_KEY` and never uses the
   service key; `_identity` builds only when enabled. 5 passed on the patched tree.

## SCHEMA REQUEST SR-AP01-1 (for the Lab control unit and an `infrx_runtime` login)

`PgIdentity` reads/writes tables directly with `service_role` privileges (as `PgAccessStore` and
`PgSignup` do), which the gateway's pool has (proven on the Supabase image). The Lab control unit logs
in as `infrx_lab_control` (EXECUTE on named functions only, 0043/0056/0059), so mounting
`lab_workspaces` there needs SECURITY DEFINER functions. No 0060-0064 number is api-identity's, so the
coordinator allocates one (LOCAL-ONLY header, tests/d proof on ap1):

```sql
create or replace function infrx.identity_account(p_user uuid)
returns table (is_operator boolean, verified boolean, org_id uuid, wallet boolean,
               suspended boolean, grant_amount text, granted_at timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select p.is_operator, v.verification_evidence_ref is not null,
         coalesce(w.personal_org_id, v.personal_org_id), w.wallet_id is not null,
         coalesce(o.suspended, false), e.amount::text, e.granted_at
    from public.profiles p cross join lateral infrx.verified_user(p.id) v
    left join infrx.credit_wallets w on w.owner_user_id = p.id and w.kind = 'consumer'
    left join public.organizations o on o.id = coalesce(w.personal_org_id, v.personal_org_id)
    left join infrx.signup_entitlements e
           on e.user_id = p.id and e.entitlement = 'initial_signup_grant'
   where p.id = p_user $$;
create or replace function infrx.identity_user_by_email(p_email text) returns uuid
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select case when count(*) = 1 then min(id::text)::uuid end
    from public.profiles where lower(email) = lower(p_email) $$;
create or replace function infrx.identity_members(p_provider uuid)
returns table (user_id uuid, email text, role text, granted_by text, granted_at timestamptz,
               revoked_at timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select m.user_id, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at
    from infrx.provider_memberships m join public.profiles p on p.id = m.user_id
   where m.provider_org_id = p_provider and m.revoked_at is null
   order by m.granted_at, m.membership_id $$;
create or replace function infrx.identity_grant_member(p_provider uuid, p_user uuid,
  p_role text, p_by text)
returns table (user_id uuid, email text, role text, granted_by text, granted_at timestamptz,
               revoked_at timestamptz, created boolean)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare v_created boolean;
begin
  insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by)
  values (p_provider, p_user, p_role, p_by)
  on conflict (provider_org_id, user_id) where revoked_at is null do nothing;
  v_created := found;
  if exists (select 1 from infrx.provider_memberships m where m.provider_org_id = p_provider
             and m.user_id = p_user and m.revoked_at is null and m.role <> p_role) then
    raise exception 'state_conflict: another current role' using errcode = 'P0001';
  end if;
  return query select m.user_id, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at,
                      v_created
    from infrx.provider_memberships m join public.profiles p on p.id = m.user_id
   where m.provider_org_id = p_provider and m.user_id = p_user and m.revoked_at is null;
end $$;
create or replace function infrx.identity_revoke_member(p_provider uuid, p_user uuid)
returns table (user_id uuid, email text, role text, granted_by text, granted_at timestamptz,
               revoked_at timestamptz)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  update infrx.provider_memberships set revoked_at = greatest(infrx.now(), granted_at)
   where provider_org_id = p_provider and user_id = p_user and revoked_at is null;
  return query select m.user_id, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at
    from infrx.provider_memberships m join public.profiles p on p.id = m.user_id
   where m.provider_org_id = p_provider and m.user_id = p_user
   order by m.revoked_at is null desc, m.granted_at desc, m.revoked_at desc limit 1;
  if not found then raise exception 'not_found: member' using errcode = 'P0001'; end if;
end $$;
create or replace function infrx.identity_create_provider(p_slug text, p_name text, p_by text)
returns table (provider_org_id uuid, slug text, display_name text, created_by text,
               created_at timestamptz, created boolean)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare v_created boolean;
begin
  insert into infrx.provider_orgs (slug, display_name, created_by) values (p_slug, p_name, p_by)
  on conflict (slug) do nothing;
  v_created := found;
  if exists (select 1 from infrx.provider_orgs o where o.slug = p_slug
             and o.display_name <> p_name) then
    raise exception 'state_conflict: slug taken' using errcode = 'P0001';
  end if;
  return query select o.provider_org_id, o.slug, o.display_name, o.created_by, o.created_at,
                      v_created from infrx.provider_orgs o where o.slug = p_slug;
end $$;
-- each: revoke all ... from public, anon, authenticated;
--       grant execute ... to service_role, infrx_lab_control (and infrx_runtime if the gateway
--       ever logs in as it). Rollback: drop the six functions.
```

`PgIdentity` then calls these (one-line changes per method) and `lab/control/app.py` mounts
`lab_workspaces` over the unit's own `SessionActors` - a follow-up WR once the SQL is on the base.

## Deviations and open items

- Idempotency: every mutation requires and validates `Idempotency-Key` (422 without it), but replay
  and conflict come from the resource's natural key (0007's one current membership per pair, the unique
  provider slug) - durable, restart- and multi-instance-safe, yet a reused key with a different target is
  not a 409. Upgrade path (ponytail-marked in `PgIdentity`): bind the key to 0060's
  `control_idempotency` (api-schema) once merged.
- Auth failures render the R270 envelope with flow.ts's `AuthFailure` codes (`invalid_credentials`,
  `link_expired`, ...) and its copy as `message`, not `contracts.errors` codes (whose fixed messages say
  "API key"). Proposed ruling (next free R272): the auth facade's error codes are the App's
  `AuthFailure` set plus `link_invalid` and `unauthenticated`; AP-09 maps them 1:1.
- Proposed ruling: `EnvelopeRoute` (R270 rendering of FastAPI validation failures) moves to
  `infrx.gateway.control` as the shared route class for every wave-7 router; a coordinator edit.
- FastAPI 0.141's `include_router` adds an `_IncludedRouter` node to `app.routes`; the four modules put
  their routes on the app's own table (as `lab_datasets` does), so `assert_route_table` and AP-00's route
  inventory can read them. AP-00's export should expect the same of other lanes.
- The GoTrue stub is an in-process ASGI app (`httpx.ASGITransport`), runnable under uvicorn; the outage
  case uses a real refused TCP connection (127.0.0.1:9).
- The callback does not claim the signup grant (contracts.md: no grant in the auth transport); AP-03's
  `POST /console/v1/signup-grant/claim` owns it, so the App's `afterSignIn` claim moves there in AP-09.
- Slices 01b-01d landed in one commit (they share the actor source and the store).
- `/lab/v1/capabilities` features report the composing process's switches; on the Lab unit (families
  forced on) the unit's composition should pass its own view when SR-AP01-1 lets it mount there.

## Estimate (remaining for AP-01)

optimistic 2 h / likely 4 h / pessimistic 7 h, confidence medium. Basis: 01a-01d took about 1 h of
wall clock in this lane (tests first, 61 mutants, PostgreSQL plain + Supabase image); remaining is
SR-AP01-1 (six functions + tests/d proof on ap1 + `PgIdentity` switch-over, 1.5-3 h), binding
`Idempotency-Key` to 0060 after api-schema merges (0.5-2 h), the Lab-unit mount WR (0.5-1 h) and
re-proving after the coordinator applies WR-AP01-1 (0.5-1 h).
