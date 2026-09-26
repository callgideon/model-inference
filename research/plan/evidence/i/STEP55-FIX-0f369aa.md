# STEP55-FIX: the W10b scripts match the better behaviour (lane evidence)

- Lane STEP55-FIX (task E4C window scripts, support lane before the RELEASE freeze), branch `codex/step55-fix`, worktree `.claude/worktrees/codex-step55-fix`.
- Base `88cb89eb`; code head `0f369aa` (`0f369aa96223383c2576332a34a0fe3658f28fd4`). This file and the coordinator update are committed after it.
- No box, AWS, SSM or hosted access. The steps ran locally against a fake `aws`/`docker`/`systemctl`/`curl` on PATH and a scratch env file. The step's own embedded Python ran on a fake psycopg (roles file). One case ran it on task-local PostgreSQL 16 (`INFRX_D_TASK=e1c`, 55449; the harness removed its container at exit).

## Changed paths (`git diff --stat 88cb89eb..0f369aa`, owned only)

`infra/rollout/steps/55-runtime-login.sh`, `infra/rollout/steps/56-resume.sh` (new, 0755), `infra/rollout/e4c-certify.sh` (the SSM read only), `infra/rollout/README.md` (row 8b, new row 8c, one log line), `infra/runbooks/rollout.md` (W10b cells, one log line), `apps/infrx-api/tests/i/test_ops_steps.py`, `test_rollout.py`, `mutants.py` (login_/launcher_ entries). `tests/integration/backend/recovery/test_runbooks.py` needed no change: no pinned phrase moved.

## Per finding

Fails-before = the new oracle run against the base script (`88cb89eb:infra/rollout/steps/55-runtime-login.sh` through the committed helpers `_login_world`/`_verifiers`, scratch driver). Passes-after = the same at head.

| Finding | Defect (base) | Fails-before | Change (head) | Passes-after | Mutant (killed) |
|---|---|---|---|---|---|
| CS-4 | ALTER ROLE ran before the `unchanged` check, so every rerun re-set both SCRAM verifiers (new salt) | rerun: exit 0, verifiers changed (fake psycopg); on PostgreSQL 16 `pg_authid.rolpassword` changed for both roles ("the rerun ALTERed a role") | the program first logs in as each role with its SSM password on :6543 (`whoami`); only `psycopg.OperationalError` is caught; ALTER then re-check only when that login fails (first run NOLOGIN, rotated password). `:74-94` | rerun: `unchanged`, verifiers equal, no restart; rotated monitor password: only `infrx_monitor` re-set, one restart; PostgreSQL: first run sets both, rerun identical output and verifiers | `login_rerun_realters`, `login_probe_failure_escapes` |
| CS-5a | DSN-count check shared exit 3 with envcheck | exit 3 | exit 5, "nothing staged" (`:97-98`) | exit 5, env file untouched, no staged copy | `login_dsn_count_is_exit_3` |
| CS-5b | a failed `systemctl restart` exited 1 after the rename, no put-back | exit 1, env file = new, 1 restart | `|| put_back ...` (`:137-138`): saved file back, restart on it, both `/readyz` probed, exit 4, stderr states the recovery | exit 4, env file = before, 2 restarts, 8001 and 8002 probed after the put-back | `login_failed_restart_not_put_back`, `login_unready_kept` (anchor moved) |
| F2 (55) | a missing SSM parameter exited with aws's code | exit 254, name absent | `value=$(aws ...) && [ -n "$value" ] || { echo "cannot read SSM parameter <name>..."; exit 2; }` (`:47-50`) | exit 2, the parameter name in stderr, no docker/systemctl call, no value printed | `login_ssm_read_unchecked` |
| F2 (certify) | a missing `pg_journal_url` exited with aws's code | exit 254 (`test_e4c_certify__...` fails at the new block) | `ops_param` + `|| { ...; exit 2; }` (`e4c-certify.sh:33-36`), before `docker ps`/`docker run` (`:44-45`) | exit 2 naming `OPS_DSN_PARAM`'s value (a name), no `run`/`ps` docker call | `launcher_ssm_read_unchecked` |
| F4 | `curl` without `--max-time`; after a put-back nothing was probed | base has no `--max-time` (grep 0); main login case fails at "curl after the last systemctl" | `unready()` probes each port with `--max-time 5` (`:118-129`); `put_back` restarts and runs `unready` again, stderr says whether they answered | every curl carries `--max-time 5`; probes follow the put-back restart | `login_probe_unbounded`, `login_put_back_unprobed` |
| F5 | staged copy (both DSNs) left on failures after `mktemp` other than the cmp/envcheck branches | backups dir uncreatable: exit 1, 1 leftover `marlin2b-gateway.env.*` | the EXIT trap also removes `$staged` (`:44-45`); the two explicit `rm -f "$staged"` dropped | exit 1, 0 leftovers | `login_staged_left` |

## The new step (RB4-1)

`infra/rollout/steps/56-resume.sh`: `: "${RELEASE:?the release commit}"` then `/root/infrx-deploy-$RELEASE/apps/infrx-api/deploy/drain.sh resume`. It does no checkout, unlike `91-abort.sh`. Runs as `infra/rollout/ssm.sh infra/rollout/steps/56-resume.sh RELEASE=$RELEASE`. Test `test_ops_resume__the_w10b_resume_runs_the_release_s_drain_and_checks_nothing_out`: refused without RELEASE; with it, only that release's `drain.sh resume` runs, and no git/systemctl call. Mutant `login_w10b_resume_pauses`. `test_rollout.py`'s step list now includes it (fails before: the list differs).

## Sentences changed

- `rollout.md` W10b step cell: the one-line-local-file resume becomes `infra/rollout/ssm.sh infra/rollout/steps/56-resume.sh RELEASE=$RELEASE`. The "never 91-abort.sh" clause is kept (RB4-1).
- W10b verify cell: "never rerun just to verify ... re-set both roles' SCRAM verifiers" becomes "a rerun is a true no-op: each role is first tried with its SSM password on :6543 and ALTERed only when that login fails ... prints `unchanged`, sets no password and restarts nothing" (CS-4).
- W10b rollback cell:
  - exit 4 now covers a failed restart as well as not ready. In both cases the step puts the file back, restarts and probes both `/readyz` (`--max-time 5`) (CS-5, F4).
  - Exit 2 names an unreadable parameter (F2). Exit 5 means the DSN count failed (CS-5). No staged leftover survives (F5).
  - The manual copy-back and delete-leftover instructions are removed.
  - The RB4-2 clause (the edge stays in maintenance after a put-back; resume, now `56-resume.sh`, or stop; a 50-install rerun needs 95 again) is kept. The last cell still names no 91-abort/90-revert/R1/R2/R4 (rb12 green).
- README row 8b: the exit list is now 2 (names the parameter), 1, 3, 5, and 4 (a failed restart or not ready, with a put-back and probe). It adds the rerun no-op and the absence of a staged leftover, and its window order ends "then step 8c".
- New row 8c: the 56-resume.sh command and its output.
- One verification-log line appended to each of rollout.md and README (repository rule: every doc ends with an append-only log).

## Commands (`api` = `apps/infrx-api`, `uv run --frozen --no-sync`)

| Command | Exit | Result |
|---|---|---|
| `bash -n` on every `infra/rollout/steps/*.sh` and `e4c-certify.sh` | 0 | all parse |
| api: `pytest -q tests/i/test_ops_steps.py -k login` at base + new tests | 1 | 3 failed (main: put-back unprobed; rerun: "a rerun re-set a role's verifier"; failures: exit 3 not 5), 1 skipped |
| api: `INFRX_D_TASK=e1c pytest -q tests/i/test_ops_steps.py -k postgresql` at base script | 1 | "the rerun ALTERed a role" (rolpassword moved for both) |
| api: `pytest -q tests/i/test_rollout.py -k e4c_certify` with the base launcher | 1 | exit 254 on the unreadable parameter |
| api: `pytest -q tests/i/test_ops_steps.py` / `tests/i/test_rollout.py` | 0 / 0 | 13 passed 1 skipped (the PostgreSQL case without INFRX_D_TASK) / 12 passed |
| api: `INFRX_D_TASK=e1c pytest -q tests/i/test_ops_steps.py` | 0 | 14 passed |
| api: `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k "login_ or launcher_ or well_formed or every_case"` | 0 | 22 passed (16 login_ + 4 launcher_ mutants killed, list well-formed, every case covered) |
| api: `pytest -q tests/i --deselect tests/i/test_mutants.py` | 0 | 192 passed, 1 skipped, 1 xfailed |
| api: `pytest -q tests/w/test_p25_runbooks.py`, `../../models/marlin2b/tests/test_profile.py` | 0 / 0 | 2 / 25 passed |
| api: `pytest -q ../../tests/integration/backend/recovery/test_runbooks.py`, `../../tests/integration/ops` | 0 / 0 | 13 / 34 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

## Wiring request

- WR-S55-1 `models/marlin2b/results/E4C-runbook.md:148-149` (not owned). Replace "A missing `pg_journal_url` exits with the aws CLI's own code, not the launcher's 2 (`e4c-certify.sh:33-35` under `set -e`), before the certify container starts (`:44`; the image inspects at `:20-23` have already run) (E4C-RUNBOOK-2 F2; RB4-4)." with "An unreadable `pg_journal_url` exits 2 naming the parameter (`e4c-certify.sh:33-36`), before the certify container starts (`:45`; the image inspects at `:20-23` have already run) (E4C-RUNBOOK-2 F2, fixed by STEP55-FIX)." Proof: `test_e4c_certify__the_launcher_passes_exactly_certify_s_box_flags_and_no_secret` (exit 2 + name, no `run`/`ps`). The log line at `:372` is history and stays.

## Open issues

- The CS-4 probe tries each login once before any ALTER, so the first run and a rotated password cost one failed auth per role on Supavisor. Supavisor's handling of auth failures, including any ban after repeated failures, is not exercised locally.
- A network failure on :6543 during the probe is treated like a failed login: the step runs the ALTER, then the re-check fails with exit 1. This matches the header ("a password already set stays set, harmless").

## Estimate

Remaining for this lane: review only. Optimistic 0.1 h, likely 0.3 h, pessimistic 1 h (a review round). Confidence high. Basis: committed, all named suites and mutants green.
