# RUNTIME-LOGIN — dedicated runtime login composes without `set role` (E3C F-1, R127, WR-I8-6)

Base `b3ba5dc7` (claude/consumer-v1) · code head `b58d4558` · branch `codex/runtime-login` ·
worktree `.claude/worktrees/codex-runtime-login`.

## Change

| Path | Change |
|---|---|
| `apps/infrx-api/infrx/gateway/pilot.py` | `DEDICATED_LOGINS` (`infrx_runtime`, `infrx_monitor`) and `dedicated_login(dsn)` (DSN user via `psycopg.conninfo`, bare or Supavisor `<role>.<ref>`); `configure_connection(..., set_role=True)`; `connection_pool` passes `set_role=not dedicated_login(DATABASE_URL)`. Dedicated login: only `set statement_timeout` (off 6543), never `set role`. Every other login (bda1586's broad DSN) unchanged. The worker shares `connection_pool`, so `worker/__main__.py` needs no edit. |
| `apps/infrx-api/tests/g/test_composition.py` | `test_f_base__a_dedicated_login_pool_sets_no_role` (docker-free). |
| `apps/infrx-api/tests/g/mutants.py` | mutant `dedicated_login_sets_role` (`set_role=True`). |
| `apps/infrx-api/tests/d/test_composition_pg.py` | `test_f_base__the_pilot_serves_and_the_worker_connects_on_the_dedicated_login` (real PG). |

No env name added: the rule is derived from the DSN user alone (0021 fixes the role names), so
the `INFRX_ENV_SCHEMA` digest is unchanged and the box's current env file stays valid (R119
rollback). `INFRX_DB_SET_ROLE` override not added — add only if an operator ever renames the login.

## Failed-then-passed regressions

| Test | Against old behaviour | After |
|---|---|---|
| `test_f_base__a_dedicated_login_pool_sets_no_role` | FAIL (pool SET the role on `infrx_runtime`) | PASS |
| `test_f_base__the_pilot_serves_and_the_worker_connects_on_the_dedicated_login` (pilot.py forced `set_role=True`) | FAIL: `RuntimeMisconfigured: unreachable at startup: journal, price_source` | PASS |

The PG case: fresh DB (0001–0021 + Marlin seed + `pgtesting.seed_credit_world`), `alter role
infrx_runtime login password` with `secrets.token_urlsafe(24)` held in memory only; gateway from
settings on that DSN (CREDIT regime): `/readyz` 200, `/v1/models` lists `nemostation/marlin-2b`,
`POST /v1/jobs` 202 and a row in `infrx.jobs`; worker `compose()` from env on the same DSN: pool
opens, `select current_user` = `infrx_runtime`.

## Commands (all from `apps/infrx-api` unless noted)

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `make api-env` (repo root) | 0 | pinned env (first `uv run` had made an extras-less venv; rerun fixed it) |
| 2 | `uv run --frozen pytest -q tests/g --ignore=tests/g/ops -p no:cacheprovider` | 0 | 584 passed |
| 3 | `uv run --frozen python -m tests.g.mutants dedicated_login_sets_role pool_configure_dropped pool_statement_timeout_zero` | 0 | 3/3 killed |
| 4 | `INFRX_D_TASK=m5 INFRX_M_S3_ENDPOINT=http://127.0.0.1:55470 INFRX_M_S3_LOCAL_CREDS=1 INFRX_M_S3_BUCKET=infrx-m5 uv run --frozen pytest -q tests/d/test_composition_pg.py tests/m/test_upload_wiring.py -p no:cacheprovider` | 1 | 7 passed, 1 failed: `test_f_base__create_app_composes_the_pilot_from_settings_on_postgresql[legacy_usd]` (`unreachable at startup: price_source`) — **pre-existing**: fails identically with base `b3ba5dc7` pilot.py restored (checked); not this lane |
| 5 | `INFRX_D_TASK=i8 uv run --frozen pytest -q tests/i/test_pooler.py -p no:cacheprovider` | 0 | 10 passed (i8 stand-in free) |
| 6 | `INFRX_D_TASK=i8 uv run --frozen python tests/i/mutants.py connector_sets_role_on_6543 connector_prepares` | 0 | 2/2 killed |
| 7 | (repo root) `INFRX_E3C_RUNTIME_LOGIN=1 make backend-local E3C_ARGS="--only s10"` at `b58d4558`, detached | 2 (runner 3) | **s10 PASS** 4 passed / 71 deselected in 20.65 s: `test_s10_the_runtime_login_cannot_become_an_owner_or_rewrite_money` (box serves on `infrx_runtime`), browser surface, operator/consumer lanes, `nc-roles-browser` PASS; gate NOT RUN only because s12 etc. deselected; head clean start=end; teardown removed all 4 `infrx-e3c-*` containers; logs contain no password |

## Wiring requests
None (all edits in owned paths).

## Open issues
- Pre-existing `test_composition_pg[legacy_usd]` red on the base (price_source probe in legacy
  regime on the 0021 DB) — owner D10/G7, not F-1.
- F-2 (stalled PG/S3 acceptance) untouched.
- Full-matrix `INFRX_E3C_RUNTIME_LOGIN=1 make backend-local` not run here (s10 only).

## Estimate
Remaining for this lane: optimistic 0 h / likely 0.5 h (review round) / pessimistic 2 h;
confidence high; basis: fix is 1 derivation + 1 flag, proven docker-free, on real PG and in E3C s10.

## Log
- 2026-09-25: written at code head b58d4558.
