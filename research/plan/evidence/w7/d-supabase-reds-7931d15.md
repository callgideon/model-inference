# d-supabase-reds (wave 7 batch 4) — AP-00 verification: the Supabase-image reds

- Base 29b9df84 (main = claude/consumer-v1). Code head 7931d15 (branch `codex/w7-d-supabase-reds`). Key `ap0`
  (PostgreSQL 57550, containers `infrx-ap0-postgres` / `-supabase`, created and removed by `tests/d/pgharness.py`;
  the self-test's decoy `infrx-ap0-dharness-postgres` on 27550). Images: plain `postgres@sha256:33f923b0…` (16.14)
  and `INFRX_D1_IMAGE=supabase` (`supabase/postgres@sha256:7768d0d1…`, 17.6). Nothing hosted, no box, no AWS/SSM/S3,
  no Vercel, no secret; no switch, no migration, no product code touched.

## The one cause

On the Supabase image the harness login `postgres` is not a superuser (`rolsuper=f`, `supabase_admin` is) and
holds every role the migrations create WITH ADMIN only (PostgreSQL 16+: `set_option=f`, `createrole_self_grant`
empty). So `set [local] role infrx_runtime|infrx_lab_control` is refused 42501 ("permission denied to set role")
and `set [local] session authorization …` needs a superuser outright. No grant is missing from the migrations and
the product code is not involved: the checks impersonated the logins in a way only a superuser can. (Row 88's
diagnosis, api-schema-2-4c65973.md, is the same class; its fix in `test_upgrade_lab.py` stays.)

## Fix (at the seam: the harness + the checks' impersonation; no oracle changed)

- `tests/d/pgharness.py`: `become(conn, role)` = `grant <role> to current_user with inherit false, set true` +
  `set local role <role>` in the caller's transaction (rolled back with it; INHERIT false leaves the harness
  login's own privileges alone). `login(role, database)` = a context manager yielding the DSN of a session that
  LOGS IN as the role (LOGIN + a one-off password in this run's container; on exit the LOGIN bit as found, no
  password) — the faithful stand-in for `set session authorization`: its session user is the role, so a
  `set role service_role` from it is still refused as in production (a `set role` stand-in would have let it
  through, i.e. weakened d8's `service_role` refusal).
- `test_d8_requests.py`: `as_runtime` → `become`; the control-login check reads `pg_roles` (rolcanlogin, limit,
  bypassrls, super) BEFORE `login` touches the role, then runs the factory's calls and the twelve refusals on
  real `infrx_lab_control` sessions. `as_control_login(dsn)` now takes `login`'s DSN.
- `test_l3sql_reads.py`: the control-login reads and refusals and the router-functions check run on real
  `infrx_lab_control` / `infrx_runtime` sessions. `test_l3sql_control.py`, `test_code_mutants_lc2.py`: `become`.
- `test_code_mutants_lw8.py`: the worker-claim refusal probe used a bare `set local role` — on Supabase it
  "passed" because the SWITCH was refused 42501, not the claim (vacuous). Now `become`: the claim's own 42501.
- `tests/d/test_pgharness.py`: `test_a_role_is_assumed_and_logged_in_as_on_either_image_and_put_back` runs the
  harness file under test on the decoy in a child (so the mutant runner's copy is the one exercised, and the
  task's port lock stays the suite's): current_user switched, no grant outlives the transaction, the login's
  session_user is the role and cannot `set role postgres` (42501), LOGIN restored, no connection after.
- `tests/integration/test_harness.py` (the production test, a different cause: `api-lifecycle.sh` names
  `AWS_SECRET_ACCESS_KEY` only in `env -u` to strip it): the scan drops `-u NAME` tokens before matching; any
  other mention in any file is still caught (two allow-list cases: `strips.py` clean, `strips_then_reads.py`
  caught). No allow-list entry was added.
- `tests/i/lab` `r151_post_check_file_name`: already green at the base 29b9df84 (the `50-lab-role.sh` case was
  fixed by merge 39065ef0); nothing to change, rerun recorded below.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| RED: `INFRX_D_TASK=ap0 INFRX_D1_IMAGE=supabase pytest tests/d/test_d8_requests.py test_l3sql_reads.py test_l3sql_control.py test_code_mutants_lc2.py test_code_mutants_lw8.py -k 'not test_mutants and not mutant_is_killed'` with the base's five files | 1 | 10 failed, 36 passed: d8 `check_release_active_answers_the_running_head_with_pins`, `check_release_eligibility_is_current_and_default_deny`, `check_assignments_are_recorded_once_for_the_runtime`, `check_the_control_login_is_bounded_and_lab_only`; l3sql_reads `check_the_control_login_holds_what_operations_reads`, `check_the_router_functions_are_the_runtime_logins_alone`; l3sql `check_the_operator_door_is_the_profiles_bit_on_the_control_login`; lc2 `check_the_runtime_login_reads_consent_through_the_rpc`, `check_a_key_answers_only_under_its_own_org`, `check_the_head_is_the_newest_version_revoked_or_not` (= api-schema-3's 10) |
| same five + `test_pgharness.py` + `test_upgrade_lab.py` at the head, Supabase / plain | 0 / 0 | 61 passed / 61 passed |
| RED: repo root `pytest tests/integration/test_harness.py` at the base | 1 | 47 passed, 1 failed (`test_nothing_in_this_directory_points_at_production`: `('api-lifecycle.sh', 'AWS_SECRET_ACCESS_KEY')`) |
| same at the head | 0 | 48 passed |
| `INFRX_D_TASK=ap0 INFRX_D1_IMAGE=supabase pytest tests/d tests/i -k 'not test_mutants' -rfE` (at 1afa778; only test_pgharness.py changed after, rerun below) | 0 | 2648 passed, 9 skipped, 92 deselected, 9 xfailed, 0 failed (1:02:51) |
| same on plain | 0 | 2649 passed, 8 skipped, 92 deselected, 9 xfailed, 0 failed (54:38) |
| `pytest tests/d/test_pgharness.py` at 7931d15, plain / Supabase | 0 / 0 | 8 passed / 8 passed; beside `test_upgrade_lab.py` in one process: 2 passed (no lock clash) |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap0 pytest tests/d/test_code_mutants_{d8,l3sql,lc2,lw8}.py`, plain / Supabase | 0 / 0 | 262 passed / 262 passed (every SQL mutant still killed through the reworked checks; every-case guards green) |
| `INFRX_MUTANTS=all pytest tests/i/lab/test_mutants.py -k 'r151 or well_formed or every_case'` | 0 | 7 passed (incl. `r151_post_check_file_name`) |
| WR-DSR-1 applied temporarily: `tests/integration/mutants.py --only e2m77/e2m78/e2m64`, plain and Supabase | 0 | all killed on both images (reverted after) |
| manual mutants on `pgharness.py` (test_pgharness role case): no `set local role` / restore always LOGIN | — | killed on both images; "no grant" killed on Supabase only (superuser on plain: by construction) |
| a `pg_has_role(...,'SET')` guard before the grant (first draft) | — | its "always grant" mutant SURVIVED on both images (the login holds ADMIN on service_role too) → guard deleted (1afa778) |
| `tests/integration/test_harness.py` manual mutants: no `-u` stripping / over-broad stripping | — | 2 failed / 1 failed (killed) |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 0 | 7 passed |
| `make api-lint` | 0 | All checks passed (ruff 0.15.12) |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458; no change: infrx/deploy untouched) |

Skips (9 Supabase / 8 plain) were not itemised (`-rfE`); none is in the five reworked files (their runs above
are all-pass, no skip).

## Wiring request

- WR-DSR-1 (`tests/integration/mutants.py`, coordinator-owned): `research/plan/evidence/w7/d-supabase-reds-WR-DSR-1.patch`
  adds e2m77 (become() actually switches) and e2m78 (login() restores NOLOGIN), both naming
  `test_a_role_is_assumed_and_logged_in_as_on_either_image_and_put_back`. Composed test:
  `INFRX_D_TASK=ap0 apps/infrx-api/.venv/bin/python tests/integration/mutants.py --only e2m77` (and e2m78) → `killed`,
  with and without `INFRX_D1_IMAGE=supabase`. The "no grant" mutant is not proposed: it survives on plain by
  construction (the plain image's login is a superuser).

## Open items (not this lane's paths)

- `tests/r/routing/test_routing_pg.py:43` still uses `set session authorization infrx_runtime` (same class; it runs
  on key r1, outside `tests/d tests/i`). One-line adoption: `with pgharness.login("infrx_runtime", DB) as dsn:` around
  its uses and connect on `dsn`. Owner: tests/r.
- The inline self-grant copies in `test_upgrade_lab.py`, `test_upgrade_0065_mutants.py`, `test_upgrade_0068_mutants.py`
  could adopt `pgharness.become`; left as they are (green on both images, anchors of their own mutants).

## Proposed ruling text (unnumbered)

A `tests/d` check that acts as a login role does so through `pgharness.become` (same transaction, role SET taken
by the harness login and rolled back) or `pgharness.login` (a real session of the role) — never a bare
`set role` or `set session authorization`, which only a superuser can run and so fail, or pass vacuously, on the
Supabase image.

## Estimate (remaining for this lane)

optimistic 0 h / likely 0.25 h / pessimistic 1 h; confidence high. Basis: every oracle green on both images at
the head (~4.5 h spent against 3/5/9, two full runs ≈ 2 h of it); left is WR-DSR-1 at merge and tests/r's one line.
