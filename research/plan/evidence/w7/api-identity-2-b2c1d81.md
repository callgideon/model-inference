# api-identity-2 (AP-01 remainder) - lane evidence at b2c1d81

Lane api-identity-2, wave 7 batch 2, branch `codex/w7-api-identity-2`, worktree
`.claude/worktrees/codex-w7-api-identity-2`, base `b05eb6f4`, code head `b2c1d813`.
Task-local key `ap1` (PostgreSQL 57551) only; d1/55432 never used (a foreign `infrx-d1-postgres`
was refused by the harness and left alone). No hosted Supabase, box, AWS, Vercel or secret touched.

## Commits (code)

| Commit | Slice |
|---|---|
| `72c22208` | A - LR-02 backend half: CAPTCHA at every password door, `/auth/v1/availability` reports `captcha {required, provider, site_key, state}` |
| `0e0afa4a` | B - `state/identity.py` PgIdentity over 0065's functions; every mutation's `Idempotency-Key` bound to 0060 `control_idempotency`; direct-SQL fallback kept in `console/session.py` (commit message miscounts the mutants: 57 memory + 22 PG at that commit) |
| `5c0529c8` | C - `/lab/v1/capabilities` reads the served route table (the Lab unit's forced families read configured) |
| `b2c1d813` | an untyped store failure on the identity routes is a typed 503 (LDP-F3), found by the WR-AP01-2 composed test |

Changed paths: `infrx/auth_facade/{__init__,stub}.py`, `infrx/gateway/routes/{auth,lab_workspaces,operator_providers}.py`,
`infrx/console/session.py`, `infrx/state/identity.py` (new), `infrx/console/__init__.py` (AP-01's own
`EnvelopeRoute`, used only by the AP-01 routes - see deviations), `tests/ap01/` (conftest, worlds,
test_auth, test_identity, mutants, `identity_0065.sql` new).

## Commands (from `apps/infrx-api` unless noted; exit codes and counts)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap01/test_auth.py` (slice A red, 03:23Z) | 1 | 20 failed: `TypeError: AuthFacade.__init__() got an unexpected keyword argument 'captcha_provider'` |
| `INFRX_D_TASK=ap1 ... tests/ap01/test_identity.py -k "reused_key or replay_answers or claims_no_key"` (slice B red, 03:27Z, base store) | 1 | 4 failed (fake + pg): reused key answered 201/200 instead of 409; a replayed grant after revocation re-granted (201); `claims_no_key` 2 passed (a regression guard) |
| first pg run on the SR-AP01-1 DDL as published | 1 | every write 500 `AmbiguousColumn` (42702) - see SR-AP01-1 rev 2 |
| `... -k lab_capabilities` (slice C red, 03:59Z) | 1 | fake: `'disabled' == 'configured'` (control route mounted, no switch) |
| `... -k failing_store` (store-failure red, 04:07Z) | 1 | `(500, 'internal') == (503, 'dependency_unavailable')` |
| `INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01` | 0 | 78 passed: auth 20, identity 46 (13 world cases x fake/pg/pg-direct, 4 fake-only, 3 pg-only), composed 5, mutant-list guards 7; 1 visible skip (the subset list's empty pg parametrization) |
| `INFRX_D1_IMAGE=supabase INFRX_D_TASK=ap1 ... tests/ap01/test_identity.py -m pg` | 0 | 29 passed on the Supabase image |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_mutants.py` (at 0e0afa4a) | 0 | 85 passed = 57 memory + 22 PG mutants killed, 0 survivors; well-formed / every-case / list-separation / 3 runner self-tests |
| same, final list at b2c1d81 | 0 | 87 passed = 59 memory + 22 PG mutants killed, 0 survivors (14 min); guards + self-tests green |
| `make api-lint` (repo root) | 0 | All checks passed (ruff 0.15.12, no per-file ignore) |
| `make api-typecheck` (repo root) | 0 | `pyright: 458 errors (baseline 458)`; 0 in the lane's files |
| `uv run --frozen pytest -q tests/g tests/contracts tests/i/lab_control tests/ap08 tests/ap03 -k "not mutant" -m "not pg" --ignore-glob='*_pg.py'` | 1 | 2262 passed, 1 failed = `test_ap00_export_artifacts_are_current` (the artifacts need regenerating: WR-AP01-4); the pg-marked modules of those tracks default to d1 (foreign container) and were not run |
| WR-AP01-2/3 applied in a scratch detached worktree at 5c0529c8 (+ b2c1d81's `console/__init__.py`): `pytest -q tests/ap01/test_composed_lab_unit.py tests/ap01/test_composed.py tests/i/lab_control tests/contracts/test_config_and_imports.py tests/g/test_startup.py` | 1 then 0 | first 435 passed / 1 failed (the unit's identity route answered 500 InsufficientPrivilege - fixed in b2c1d81); then the composed files 12 passed |
| scratch: `python -m infrx.contracts.openapi.export` then `pytest tests/contracts/test_openapi_export.py` | 0 | consumer.json +102/-50; 12 passed |

## API-IDENTITY / API-KEYGRANT (where each oracle lives)

Idempotency (0060): same key + other body -> 409 `idempotency_conflict`, nothing written
(`a_reused_key_with_another_request_is_409_and_writes_nothing`, grant/revoke/onboarding); replay answers
the first outcome and never redoes it - a replayed grant after a revocation does not re-grant, a replayed
revocation after a new grant does not revoke (`a_replay_answers_the_first_outcome_and_never_redoes_it`);
a refused write claims no key (`a_refused_mutation_claims_no_key`); one `control_idempotency` row scoped
`provider:<workspace>/lab.member_grant` with the path+body hash and one `succeeded` operation
(`test_identity_pg__a_first_mutation_is_one_finished_operation_under_its_key`); the key is per action.
Lab login: `test_identity_pg__the_lab_login_runs_the_identity_doors_and_reads_no_table` (PgIdentity on
`infrx_lab_control`, no `set role`: reads, grant, revoke, onboarding through 0065 + 0060 only; the login
gets `InsufficientPrivilege` on profiles / provider_memberships / provider_orgs / control_idempotency).
LR-02 (enumeration re-proven with the challenge on): `a_required_challenge_guards_every_password_door_and_reveals_no_account`
(sign-in, sign-up, recovery without a token: 422 `captcha_failed`, byte-identical for a known and an
unknown address; solved: proceeds; sign-in forwards `gotrue_meta_security`). The earlier matrix
(expired token, forged provider, wrong audience, revoked member, self-elevation, CSRF, no secrets in
logs) is unchanged and green in all three worlds.

## WIRING REQUESTS (coordinator; exact patch `research/plan/evidence/w7/api-identity-2-b2c1d81-WR-AP01-2-3.patch`, applies cleanly on b2c1d81)

- **WR-AP01-2** `infrx/lab/control/app.py`: with `IDENTITY_API` (default off) `_compose` builds
  `state.identity.PgIdentity(connect)` on the unit's login and `SessionActors(sessions, identity,
  origins=WEB_ORIGINS)` (no key door), sets `rt.actors/identity/lab_access`; `create_app` registers
  `lab_workspaces` and `console_me` after `lab_reviews`. Composed test `tests/ap01/test_composed_lab_unit.py`
  (in the patch, 7 cases): off -> `/lab/v1/workspaces` 404; on -> SessionActors + PgIdentity, 401 without a
  session, the store dialled only on `INFRX_LAB_DATABASE_URL` with `set_role=False`, a store refusal is
  503, a foreign origin's member mutation 403; each FEATURES route exists on the unit. It also gives
  LAB_JUDGE_API its `rt.actors` (AP-08's routes stop answering 503). REQUIRES 0065 on the base (the
  unit's login has no table grants): apply with or after api-schema-2's 0065.
- **WR-AP01-3** `infrx/config.py` `DeploymentSettings`: `auth_captcha_provider: str = ""`,
  `auth_captcha_site_key: str = ""` (env `AUTH_CAPTCHA_PROVIDER`, `AUTH_CAPTCHA_SITE_KEY`, public values);
  `pilot._auth_facade` passes them; pins `tests/contracts/test_config_and_imports.py` DEPLOYMENT_EXPECTED
  and `deploy/preflight.py` NOT_SETTABLE; `tests/ap01/test_composed.py` asserts `captcha_widget`.
- **WR-AP01-4** regenerate `apps/infrx-api/openapi/*` (`uv run --frozen python -m infrx.contracts.openapi.export`)
  and `packages/api-client` (`make api-client-test`): `AuthAvailability.captcha`, `SignIn.captcha_token`,
  `Captcha`. With WR-AP01-2, `export.py`'s `lab-control` composition should set `IDENTITY_API` over an inert
  identity stand-in so the unit's artifact documents `/lab/v1/workspaces|capabilities|members` and
  `/console/v1/me|capabilities`.
- **WR-AP01-5 (at the 0065 merge)** `infrx/gateway/pilot.py` `_identity`: import `PgIdentity` from
  `..state.identity` (not `..console.session`); then delete the fallback (`console/session.py` ACCOUNT ...
  `class PgIdentity`), `tests/ap01/identity_0065.sql` + its conftest branch, the `pg_direct` world param,
  and the fallback-only PG mutants (`pg_account_*`, `pg_email_case_sensitive`, `pg_retry_reads_created`,
  `pg_revocation_in_the_future`, `pg_revoked_listed`, `pg_repeat_revocation_refused`,
  `pg_provider_retry_reads_created`, `pg_role_change_overwrites`, `pg_existing_provider_renamed_through` -
  the last two then belong to 0065's tests/d proof). The Makefile line from WR-AP01-1 is unchanged.

## SCHEMA REQUEST SR-AP01-1 rev 2 (api-schema-2's 0065)

The DDL published in `api-identity-a5c856d.md` is WRONG as written: in the three plpgsql functions
(`identity_grant_member`, `identity_revoke_member`, `identity_create_provider`) the `returns table`
OUT columns (`user_id`, `role`, `granted_by`, `slug`, ...) are plpgsql variables, so `on conflict
(provider_org_id, user_id)`, `where ... user_id = p_user` and `on conflict (slug)` raise 42702
`AmbiguousColumn` on every call. Fix: the first line of each of those three bodies is
`#variable_conflict use_column` (exactly as `tests/ap01/identity_0065.sql`, which is the corrected DDL +
grants, proven on plain PG and the Supabase image). Grants unchanged: revoke all from public, anon,
authenticated; execute to service_role, infrx_lab_control.

## Deviations and open items

- `infrx/console/__init__.py` edited (not in the batch-2 owned list): AP-01's own `EnvelopeRoute`
  (only the four AP-01 routers use it) now renders an untyped psycopg error as 503
  `dependency_unavailable` (LDP-F3), the root fix for the composed-test 500.
- `captcha_failed` (422) is a new facade code beyond flow.ts's `AuthFailure`; the App's forms gain it with
  the widget (api-frontends-app). Proposed ruling (next free R272): the facade's codes are `AuthFailure` +
  `link_invalid`, `unauthenticated`, `captcha_failed`.
- The hosted CAPTCHA setting is not readable from GoTrue's public `/settings`; "configured" therefore
  means the deployment states the provider + public site key (WR-AP01-3). A required challenge without
  them makes sign-in/up/recovery `unavailable` (`captcha_unconfigured`). Finding: the hosted policy also
  checks password sign-in, so sign-in now forwards the token (enabling it with the old facade would have
  locked out every sign-in).
- `/lab/v1/capabilities` features now come from the served route table, not the switches (the Lab unit
  forces its families on with every `LAB_*` switch off); `console/v1/capabilities` keeps the switches.
- A replay answers 200 with the first body (the first call's 201 is not replayed as 201), as before.
- Replay after a key's 24 h retention is 0060's 410; not re-tested here (api-schema's oracle).

## Estimate (remaining for AP-01)

optimistic 1 h / likely 2 h / pessimistic 4 h, confidence medium. Basis: this lane took ~1.3 h of wall
clock (most of it PG mutant runs); remaining is the coordinator applying WR-AP01-2..4 and, at the 0065
merge, WR-AP01-5 plus one re-run of `tests/ap01` + the mutant list on both images (0.5-1 h), and any
fallout of 0065 differing from the corrected DDL (0.5-3 h).
