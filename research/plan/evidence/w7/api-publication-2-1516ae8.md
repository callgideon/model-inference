# api-publication-2 (wave 7 batch 4, AP-06; register row 103): WR-AS3-2 and WR-AS3-3 at 1516ae8

- Lane / branch / worktree: api-publication-2, `codex/w7-api-publication-2`, `.claude/worktrees/codex-w7-api-publication-2`
- Base: 29b9df84 (= main). Code head: 1516ae81. Key: `ap6` (PostgreSQL 57558; containers created/removed by `tests/d/pgharness.py`); never d1.
- Images: plain `postgres:16` (harness digest) and `INFRX_D1_IMAGE=supabase`.
- Nothing hosted, no box, no AWS/SSM/S3/Vercel, no secrets. No migration, no switch; `LAB_PUBLICATION` stays off. OpenAPI export unchanged (no route touched), so no regeneration.

## What was built

| Item | Delivered | Where |
|---|---|---|
| WR-AS3-2 | `PgDevCredentials`, the `DevCredentials` port over 0068's doors: `keys()` -> `infrx.lab_control_dev_keys` (no hash), `revoke()` -> `infrx.lab_control_revoke_dev_key` (actor `lab:<user>`, the request's Idempotency-Key; a replay answers the first `revoked_at`), `wallet()` -> `infrx.lab_control_dev_wallet` -> `DevWallet(balance=Money{amount, unit: CREDIT})`. A malformed id is `not_found` before any statement (otherwise a 22P02 500). Refusals are `domain_error`'s typed ones. | `apps/infrx-api/infrx/lab/publication/__init__.py` (new) |
| WR-AS3-3 | `lab_connector(dsn)`: an `infrx_*` login (0043/0068 shape: member of nothing) passes `set_role=False`; any other login keeps I8's rule (`None`: `set role service_role` off the pooler only). Every `connector(LAB_DATABASE_URL)` in the workers (compose + the three operator commands) uses it. The two DSN shapes are in the module docstring. | `apps/infrx-api/infrx/lab/workers/__main__.py` |
| Composition | Not mine (rule 3): WR-AP06-6 below composes `PgDevCredentials` into `Publication.credentials` on the Lab unit. The PG cases compose it exactly as the unit would (control login `infrx_lab_control`, `set_role=False`). | WR-AP06-6 |

## Red before green (seam tests first)

| Run | Exit | Outcome |
|---|---|---|
| `INFRX_D_TASK=ap6 pytest -q tests/ap06/test_publication_pg.py` with the two new cases, before any implementation (commit 3bccd7ae carries the cases) | 1 | 2 failed, 3 passed: `ModuleNotFoundError: infrx.lab.publication` (dev keys/wallet are 503 on the PG world today); `psycopg.errors.InsufficientPrivilege: permission denied to set role "service_role"` (the datasets role login on a direct DSN, through `compose(...).ready()`) |
| WR-AP06-6's composed test without the `app.py` hunk | 1 | 1 failed (credentials None), 2 passed; with the hunk applied transiently 3 passed (reverted) |

## Checks at 1516ae8

| Command (apps/infrx-api unless noted) | Exit | Outcome |
|---|---|---|
| `INFRX_D_TASK=ap6 uv run --frozen pytest -q tests/ap06 tests/g/lab_control tests/w/test_lab_workers.py -k 'not test_mutants'` (plain) | 0 | 76 passed (17 deselected = the mutant parametrizations) |
| same with `INFRX_D1_IMAGE=supabase` | 0 | 76 passed |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap6 uv run --frozen pytest -q tests/ap06/test_mutants.py` | 0 | 53 passed: 36 unit + 13 PG mutants killed (9 new), list well-formed, every-case guard green, runner self-tests |
| `uv run --frozen pytest -q tests/w/test_lab_workers_mutants.py tests/w/test_worker_main_mutants.py` (subset; anchors unmoved) | 0 | 19 passed, 1 skipped (the PG half of worker_main off its key) |
| `uv run --frozen pytest -q tests/contracts/test_openapi_export.py` | 0 | 12 passed (baseline unchanged) |
| repo root: `pytest -q tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed (no new mutant runner) |
| `make api-lint` | 0 | All checks passed (ruff 0.15.12) |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458); 0 in `infrx/lab/publication`, `tests/ap06`; `workers/__main__.py`'s 13 are pre-existing (line shifts only) |

Failed-then-passed during the lane: the first full mutant run (exit 1, 51 passed) reported `pg_role_login_switches_role` and `pg_compose_ignores_the_role_login` as broken_runner (`InsufficientPrivilege` escaped the case as an exception death, which the list forbids). The case now compares the refused login's outcome (an assertion death); the oracle is unchanged.

## New mutants (`tests/ap06/mutants.py` PG_MUTANTS)

`pg_dev_credentials_malformed_id_queried`, `pg_dev_credentials_key_id_unguarded`, `pg_dev_revoke_actor_dropped`, `pg_dev_revoke_unkeyed`, `pg_dev_wallet_always_opened`, `pg_dev_wallet_in_usd` (case `test_publication_pg__dev_keys_and_the_wallet_answer_from_0068s_doors`); `pg_role_login_switches_role`, `pg_service_login_never_switches`, `pg_compose_ignores_the_role_login` (case `test_publication_pg__a_role_login_connects_without_the_switch_and_the_service_login_switches`).

## Wiring requests

**WR-AP06-6** `apps/infrx-api/infrx/lab/control/app.py` `_compose`: `Publication(operations, PgControlOps(connect), credentials=PgDevCredentials(connect))` (`from ..publication import PgDevCredentials`); the unit's login `infrx_lab_control` holds the three doors' EXECUTE (0068). Exact patch with the composed test `tests/ap06/test_composed.py::test_composed__the_lab_unit_composes_0068s_dev_credentials`: `api-publication-2-1516ae8-WR-AP06-6.patch` (beside this file). With it the AP-06 dev-key list/revoke and dev-wallet routes answer from PostgreSQL instead of 503 once `LAB_PUBLICATION` is on (it stays off).

## Schema requests

None.

## Notes / open

- The role-login case lives in `tests/ap06/test_publication_pg.py` (real login on ap6) rather than `tests/w/test_lab_workers.py`: a new tests/w case would need a mutant in `tests/w/lab_workers_mutants.py`, which this lane does not own. `tests/w/test_lab_workers.py` is unchanged and green.
- The `infrx_` prefix rule covers 0021's dedicated logins too (they are also members of nothing). The pooler shape is unchanged: nothing is SET there.
- E4-ON o03 on the per-role logins on the box is still WR-LDP-7's.

## Estimate (remaining for this lane)

optimistic 0.5 h / likely 1 h / pessimistic 2 h, confidence medium-high. Basis: both items done and proved on ap6 on both images (~2.5 h of 3/5/9); remaining = WR-AP06-6 at merge and one review round.
