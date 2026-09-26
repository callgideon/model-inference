# STEP55-FIX-2: no failed pooler authentication on the first run (lane evidence)

- Lane STEP55-FIX-2 (support lane after STEP55-FIX; lens minors S55F-1, S55F-2 of `STEP55-FIX-0f369aa.md`), branch `codex/step55-fix-2`, worktree `.claude/worktrees/codex-step55-fix-2`.
- Base `cc23960e`. Code commit `0a7dde4` (`0a7dde4d5510b4046c6cd0c156181124c514e988`). This file and the coordinator update are committed after it.
- No box, AWS, SSM or hosted access. The step ran locally against the fake `aws`/`docker`/`systemctl`/`curl` and the fake psycopg (roles file). One case ran its program on task-local PostgreSQL 16 (`INFRX_D_TASK=e1c`, 55449); the harness removed its container at exit (`docker ps -a` shows none).

## Changed paths (`git diff --stat cc23960e..0a7dde4`)

`infra/rollout/steps/55-runtime-login.sh` (+14/-8), `infra/rollout/README.md` (row 8b sentence, one log line), `apps/infrx-api/tests/i/test_ops_steps.py`, `apps/infrx-api/tests/i/mutants.py`. Nothing else.

## Per finding

Fails-before = the new oracles run against the base script (`cc23960e`, before the code edit). Passes-after = the same at `0a7dde4`.

| Finding | Defect (base) | Fails-before | Change (head) | Passes-after | Mutant (killed) |
|---|---|---|---|---|---|
| S55F-2 | CS-4's probe logged in as each role on :6543 before any ALTER. On the first run (0021 leaves both NOLOGIN) that is one failed pooler authentication per role from the box IP | fake psycopg: first-run log `failed infrx_runtime, alter infrx_runtime, login infrx_runtime, failed infrx_monitor, alter infrx_monitor, login infrx_monitor` ("a NOLOGIN role was probed"). PG 16: first-run logins `failed infrx_runtime, login infrx_runtime, failed infrx_monitor, login infrx_monitor` | the program reads `select rolcanlogin from pg_roles where rolname = %s` over the owner connection it already holds. A NOLOGIN role is ALTERed with no probe. Only a LOGIN role is probed (to catch a rotated password). CS-4's rerun no-op is unchanged (`55-runtime-login.sh:84-89`; header item 2 rewritten) | fake: first run `alter, login` per role (zero failed); rerun `login infrx_runtime, login infrx_monitor` (zero failed, verifiers equal, `unchanged`, no restart); rotated monitor password `login infrx_runtime, failed infrx_monitor, alter infrx_monitor, login infrx_monitor` (one failed auth, only that role re-set). PG 16: first run and rerun zero failed logins; rotation one `failed infrx_monitor`; `rolpassword` unchanged for runtime and changed for monitor | `login_first_run_probes_nologin` (`whoami(dsn) if can_login else None` -> `whoami(dsn)`), killed by `test_ops_login__a_rerun_touches_no_role_and_a_rotated_password_is_set_again` |
| S55F-1 | README row 8b: "a rerun whose logins work sets no password and prints `unchanged`". This is wrong after a W10: the file names `pg_journal_url` again, so such a rerun restarts the units | wording only | "a rerun sets no password for a role whose login works; when the env file already names both DSNs it prints `unchanged` and restarts nothing" (rollout.md's wording) | README row 8b | n/a (doc) |

How each test observes the logins:
- **Fake psycopg.** It writes `pg.log` in order: `login <role>` or `failed <role>` for each role connect, and `alter <role>` for each ALTER. It also answers the `rolcanlogin` query from the roles file.
- **PostgreSQL 16.** A preamble wraps `psycopg.connect` and prints `login`/`failed <user>` to stderr. The test keeps only the two role names, so no server log setting is needed. The case now also covers a rotated password.

## Commands (`api` = `apps/infrx-api`, `uv run --frozen --no-sync`)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env |
| api: `pytest -q tests/i/test_ops_steps.py -k login` (new tests, base script) | 1 | 1 failed ("a NOLOGIN role was probed"), 2 passed, 1 skipped |
| api: `INFRX_D_TASK=e1c pytest -q tests/i/test_ops_steps.py -k postgresql` (base script) | 1 | 1 failed (first-run logins show `failed` for both roles) |
| `bash -n infra/rollout/steps/55-runtime-login.sh` | 0 | parses |
| api: `pytest -q tests/i/test_ops_steps.py tests/i/test_rollout.py` | 0 | 25 passed, 1 skipped (the PG case without INFRX_D_TASK) |
| api: `INFRX_D_TASK=e1c pytest -q tests/i/test_ops_steps.py` | 0 | 14 passed |
| api: `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k "login_ or launcher_ or well_formed or every_case"` | 0 | 23 passed: 17 login_ (incl. the new one) + 4 launcher_ killed, list well-formed, every case covered |
| api: `python tests/i/mutants.py login_first_run_probes_nologin` / `login_probe_failure_escapes` | 0 / 0 | killed (1 failed, 1 skipped) / killed (the rotation's probe still raises OperationalError) |
| api: `pytest -q tests/i --deselect tests/i/test_mutants.py` | 0 | 192 passed, 1 skipped, 1 xfailed |
| api: `pytest -q ../../tests/integration/backend/recovery/test_runbooks.py ../../tests/integration/ops` | 0 | 47 passed |
| `ruff check` on the two test files | 0 | all checks passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

The mutant runner does not pass `INFRX_D_TASK`, so in mutant runs the PG case skips and the fake-psycopg case kills. The PG case's fails-before above is the same behaviour the mutant restores.

## Wiring request

- **WR-S55F2-1**, `infra/runbooks/rollout.md:84`, W10b verify cell. This lane does not own the file; the brief limits the diff to the step, the README, the tests/mutants and the evidence.
  - Replace: "each role is first tried with its SSM password on :6543 and ALTERed only when that login fails (the first run, a rotated password), so"
  - With: "a role pg_roles shows NOLOGIN (the first run) is ALTERed with no login attempt, and a LOGIN role is first tried with its SSM password on :6543 and ALTERed only when that login fails (a rotated password: one failed pooler authentication), so"
  - Proof: `test_ops_login__a_rerun_touches_no_role_and_a_rotated_password_is_set_again` (the `pg.log` sequences above).
  - `tests/integration/backend/recovery/test_runbooks.py` pins no part of that phrase (grep).

## Open issues

- Supavisor's response to the one failed authentication on a rotated password is still not exercised locally. It is now one attempt per rotated role per run, not one per role on every first run.
- `select rolcanlogin` returns no row when the role is missing: 0021 was not applied. `fetchone()[0]` then raises and the program exits 1 before any ALTER. The env file is untouched, and the header's exit 1 covers this.

## Estimate

Remaining for this lane: review only. Optimistic 0.1 h, likely 0.2 h, pessimistic 0.5 h (one review round). Confidence high. Basis: committed; the named suites, the PG-16 case and the mutants are green.
