# W6 lane infra-libs: evidence at fa3addbd

- Lane: infra-libs (wave W6, the v1 clean-up wave). Findings: INFRA-05, INFRA-06, INFRA-11, INFRA-04 (2, 3), DT-04, DT-15. Key `i5`; no docker was used (every case runs on PATH stubs).
- Branch `codex/w6-infra-libs`. Base `08983639`. Code head `fa3addbd`; the evidence commit follows it.
- Commits: `0944df64` (INFRA-05), `c4c2fbd4` (INFRA-06 + the 72 write_env bug), `fa3addbd` (INFRA-11, INFRA-04(2,3), DT-04, DT-15).

## What changed, by task

### INFRA-05: one coordinator-host lib
- New file `infra/rollout/host-lib.sh` (20 lines; sourced only, never run). It holds `aws()` (drops the three stale AWS_* names and passes `--region ${REGION:-us-east-1}`), `say()` (writes a UTC stamp, then tees to `${HOST_LOG:-/dev/null}`), `need_venv()` (exit 2 with the old message; sets `PY`), `ssm_value NAME`, and `ssm_to_file NAME FILE` (writes the file with mode 0600; an empty or unreadable value is a failure, the file is removed and the parameter is named).
- Scripts that now source it:

  | script | lines before → after | what moved to the lib |
  |---|---|---|
  | `ssm.sh` | 46 → 45 | aws() |
  | `operator-cli.sh` | 17 → 17 | venv check, aws(), the two SSM reads; `exec "$PY"` |
  | `hosted-migrate.sh` | 155 → 154 | aws(), say() (`HOST_LOG=$LOG`), the PGPASSWORD read |
  | `unblock-coordinator.sh` | 139 → 140 | aws() (`REGION=${AWS_REGION:-us-east-1}`); the owner/monitor reads use ssm_to_file and now refuse an empty value |

- `hosted-migrate.sh` changed only at its helper lines (44–45 and 61). EXPECTED_PENDING, the anchors, the flags and the patch context lines 32–38, 98–104, 128–134, 140–146 and 152–155 are byte-identical, so `test_known_good_proof.py`'s reverse-apply of both window patches still passes.
- `certify-h6.sh` was not changed. It has no aws(), say() or SSM read of its own (it calls operator-cli.sh), so there is nothing to deduplicate.
- Copies owned by other lanes are filed as wiring requests WR-IL-1, 2 and 3.

### INFRA-06: box lib, plus the 72-observe-install write_env bug
- New file `infra/rollout/box-lib.sh` (60 lines). It takes the generic half of `infra/lab/rollout/lib.sh`: R, say/die, at_release, param, stage_env, place and ready, plus a new `caddy_reload`. `say` writes to `BOX_LOG`, which defaults to none.
- `infra/lab/rollout/lib.sh` went from 78 to 32 lines. It now holds the Lab constants, lab_image, unit_file and health_port, and sources box-lib.sh. It sets `BOX_LOG=$LAB_LOG` and `STEP=${STEP:-lab}`, so the log and the line format are unchanged.
- `45-lab-site.sh` and `90-lab-revert.sh` call `caddy_reload` instead of their inline copies: 4 copies of the literal became 1.
- `72-observe-install.sh` went from 92 to 84 lines. Its write_env is now `stage_env` + `place … root:root`, with the stage in its own statement.
  - Before, `$(param …)` sat inside printf's argument, so a failed or empty SSM read wrote `NAME=` and the step carried on.
  - Now the step exits 2 naming the parameter. No env file and no staged copy are left, and no unit is enabled.
  - Red first: `test_rollout_host__observe_install_refuses_an_unreadable_or_empty_secret` failed at the old step with `assert (0 == 2)` (stdout `canary: BLOCKED (P-24)…`), then passed.
  - A checkout without box-lib.sh is BLOCKED (exit 3) and the message names the file.
- Not done, with reasons:
  - **need_i8**: the BLOCKED guard has to run before any lib can be sourced, because the pre-I8 targets carry no lib, so the 3-line loop stays at each site.
  - **The REPO seam in the nine literal-repo consumer steps** and **the `g()` idiom unification**: neither is needed by any test or helper here. Changing them would only churn steps that run before the checkout.
  - **55/81 pollers**: their semantics differ (deadline vs tries, 2 s sleep).
  - **The garbled 72:64-65 comment**: it is left as is, because `tests/integration/lab_observe/test_i2l_obs.py` reverse-applies WR-OBS-5.diff, which carries it as context. Fixing it turned that case red; see WR-IL-5.

### INFRA-11
- `70-lab-status.sh` takes `ROLE` (default `control`). The unit comes from `unit_file "$ROLE"` and the port from `health_port "$ROLE"`; UNIT and PORT still override either.
- `60-lab-smoke.sh` now defaults to `READY_S=30` (it was 10).

### INFRA-04 (2): lab-checkout.sh tests
- Added the `repo=${REPO:-/home/ubuntu/model-inference}` seam.
- A new case uses a real origin and clone, a recording `sudo` and a recording 40-checkout.sh. It checks:
  - the fetch of the branch comes first;
  - an unknown RELEASE is exit 2 with the "not on origin" message;
  - already at RELEASE is exit 0, with no delegation;
  - a serve.sh that differs is exit 2, with no delegation;
  - otherwise 40-checkout runs exactly once;
  - every git call is `-u ubuntu git -C <repo>`.
- lab-checkout.sh joined the strict / `bash -n` sweep.

### INFRA-04 (3) + DT-15: 70-lab-status scrub
- The new case stubs journalctl, systemctl and curl to emit a DSN, a Bearer token, password, anon_key, a secret line and a postgres URL. The canary deliberately contains no scrubbed word, so each line is dropped only by its own pattern.
- It asserts:
  - the canary never reaches stdout or stderr;
  - the unit name, NRestarts, the benign journal line and the readiness body are printed;
  - ROLE=eval gives journalctl `-u infrx-lab-eval` and port 8012.
- Red first: the ROLE=eval half failed (the output named infrx-lab-control). The default half held, which pins the existing scrub.

### DT-04
- A `chown` stub is now in the Lab box stand-in. The control-on and role-on cases assert exactly one `chown ubuntu:ubuntu <env>.staged.*`. These pin today's fix, so they were green at the old tree; the mutants below prove they bite.
- The header comment of `40-lab-control.sh` now says ubuntu:ubuntu instead of root. The two README lines are filed as WR-IL-4.

## Mutants (all with INFRX_MUTANTS=all)
- Track I (`tests/i/mutants.py`, 458 → 471):
  - +9 for host-lib: `host_aws_keeps_the_stale_keys`, `host_aws_region_ignored`, `host_ssm_file_world_readable`, `host_ssm_empty_accepted`, `host_ssm_value_on_argv_name`, `host_venv_unchecked`, `host_say_unlogged`, `host_script_own_aws`, `operator_cli_empty_secret_runs`.
  - +4 for box-lib/72: `box_empty_secret_written`, `box_failed_read_written`, `observe_install_refusal_swallowed`, `observe_install_without_the_lib`.
  - Re-pointed: `observe_install_env_world_readable` now targets box-lib's `chmod 0600 "$tmp"; echo "$tmp"`.
- Lab (`tests/i/lab/mutants.py`, 66 → 81):
  - +15: `control_env_root_owned`, `role_env_root_owned`, `env_owner_never_set`, `smoke_ready_s_short`, `status_journal_unscrubbed`, `status_readiness_unscrubbed`, `status_unit_fixed`, `status_port_fixed`, `checkout_not_strict`, `checkout_no_fetch`, `checkout_unknown_release`, `checkout_reapplies_the_release`, `checkout_engine_unchecked`, `checkout_as_root`, `checkout_not_delegated`.
  - Re-pointed to box-lib.sh: `preflight_any_checkout` and `env_file_world_readable`. The STEPS_RUNNER layout now copies `infra/rollout/box-lib.sh`.
- Three mutants first came back `broken_runner` because their death was a crash (FileNotFoundError or an unpacking error), not an assertion. These were `env_owner_never_set`, `checkout_as_root` and `checkout_not_delegated`, plus `host_say_unlogged` earlier. The cases were made crash-free, and each one is now killed by an assertion.

## Commands at fa3addbd (cwd apps/infrx-api unless noted)

| command | exit | result |
|---|---|---|
| `make api-env` (repo root) | 0 | pinned env |
| `pytest tests/i/lab tests/i/test_rollout.py tests/i/test_rollout_host.py tests/i/test_ops_steps.py tests/i/test_known_good_proof.py -k "not test_mutant_is_killed and not runner_cannot"` | 0 | 98 passed, 1 skipped (INFRX_D_TASK PG case), 4 deselected |
| `pytest tests/i/test_install.py test_migrate.py test_release_bundle.py test_envcheck.py test_prereqs.py test_alert_sns.py test_artifacts.py test_worker_unit.py` | 0 | 91 passed |
| `pytest tests/i/test_scripts.py tests/i/test_packaging.py` | 0 | 34 passed |
| `pytest tests/integration/lab_observe/test_i2l_obs.py` (repo root) | 0 | 13 passed |
| `pytest tests/i/test_mutants.py tests/i/lab/test_mutants.py -k "well_formed or every_case"` | 0 | 4 passed |
| `INFRX_MUTANTS=all pytest tests/i/lab/test_mutants.py` | 0 | 85 passed (81 mutants killed, 0 survivors, + list and self tests) |
| `INFRX_MUTANTS=all pytest tests/i/test_mutants.py::test_mutant_is_killed[<187 ids>]` (4 shards) | 0 ×4 | 47+47+47+46 = 187 killed, 0 survivors |
| `bash -n` on infra/rollout/*.sh, steps/*.sh, infra/lab/rollout/*.sh, steps/*.sh | 0 | 54 scripts |
| red: `pytest tests/i/test_rollout_host.py` before host-lib.sh | 1 | 5 failed, 1 passed (operator-cli pin held) |
| red: the 72 case before box-lib | 1 | `assert (0 == 2)` |
| red: `pytest tests/i/lab/test_lab_rollout_steps.py` before INFRA-11/04 | 1 | 3 failed: smoke `10 == 30`, status ROLE=eval named control, checkout `cannot change to /home/ubuntu/model-inference` |

- The I-list subset is every mutant whose file is one this lane touched (ssm, host-lib, box-lib, operator-cli, hosted-migrate, unblock, 72) or whose cases live in test_rollout_host, test_ops_steps, test_rollout or test_known_good_proof. Mutants naming an i8-stack case are excluded.
- **Reason for the subset:** the full I list and `make api-test` start the `i8` PostgreSQL/pgbouncer stack, and this lane's isolation is key `i5`. The other 284 I mutants mutate files this lane did not touch.

## Behaviour notes (contracts kept)
- Every step's exit codes are unchanged; tests/i/lab/test_lab_rollout_steps.py and test_ops_steps.py pass unchanged.
- These are the only deltas:
  - 72 now refuses an empty or unreadable SSM value (exit 2). That was the bug.
  - 72 on a checkout without box-lib.sh is BLOCKED (exit 3), a new BLOCKED reason at pre-W6 checkouts.
  - 72's "wrote" line has say's stamp form.
  - 72's SSM names must be `/…` (stage_env's rule); every documented name is.
  - 60's default wait is 30 tries.
  - 70 takes ROLE.
  - unblock-coordinator refuses an empty owner DSN or monitor password.
  - hosted-migrate and operator-cli honour a `REGION` in the environment (default us-east-1, as before).

## Wiring requests
- **WR-IL-1** (lab-release-tool), `infra/lab/rollout/launch-v1.sh` (or lab-release.sh):
  - Replace `:40 aws() { command env -u … aws --region us-east-1 "$@"; }` with `. "$(dirname "${BASH_SOURCE[0]}")/../../rollout/host-lib.sh"` (keep its `say` banner as `banner()` or keep the local say after the source).
  - `:167` → `VERCEL_TOKEN=$(ssm_value "$SSM_VERCEL")`.
  - `:173` → `ANON=$(ssm_value "$SSM_ANON")`.
  - Test: test_rollout_host's `HOST_SCRIPTS` gains the path.
- **WR-IL-2** (certify-release), `infra/rollout/certify-window.sh`:
  - `:44` → `need_venv`.
  - `:49-50` → `. "$(dirname "${BASH_SOURCE[0]}")/host-lib.sh"; HOST_LOG=$LOGDIR/window.log`.
  - `:101` secret_to_file → `ssm_to_file` (its `[ -s ]` passes an empty value, because `--output text` writes "\n").
  - `go-live-remaining.sh` is being retired by that lane; there is nothing to wire there.
- **WR-IL-3** (owner of apps/infrx-api/deploy), `release-bundle.sh:24-25`: replace aws() with `. "$(dirname "${BASH_SOURCE[0]}")/../../../infra/rollout/host-lib.sh"`; test_release_bundle unchanged.
- **WR-IL-4** (docs-state), `infra/lab/app/README.md`:
  - `:44` → `` Lab control (`/etc/infrx-lab-control.env`, mode 0600, owned by ubuntu:ubuntu - the unit's User=ubuntu reads it as docker --env-file) [OP]: ``
  - `:111` → `sudo install -m 0600 -o ubuntu -g ubuntu /dev/stdin /etc/infrx-lab-control.env < lab-control.env`
- **WR-IL-5** (tests/integration owner): when WR-OBS-5.diff is retired from `test_i2l_obs.py`, fix the garbled comment at `72-observe-install.sh:64-65` to: `# I2L-OBS (WR-OBS-5): the Lab observe rules and exporter, which the observe cycle and the Lab` / `# trace-gauges unit read from this copy (inert until the Lab traces env file exists).`

## Open items
None blocking.

## Estimate
- Remaining work: 0.5 / 1 / 2 h (optimistic / likely / pessimistic), confidence high.
- Basis: the code is done and the lists are green. What remains is merging the wiring requests (above) and any review round.
- Spent: about 3 h against the brief's 4 / 7 / 12.

## Log
- 2026-10-01T03:24Z: written by the infra-libs implementer at fa3addbd.
