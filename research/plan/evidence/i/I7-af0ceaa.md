# I7 - Package release controls and optimization evidence operations (LAB-WORKERS, ROLLOUT-RECOVER)

Lane lab-workers (LW5), branch `codex/w5-lab-workers`, worktree
`.claude/worktrees/codex-w5-lab-workers`. Base `9a48300c`; code head `af0ceaab` (I7 commit
`ef7f2c3a`; `859b27b4` and `af0ceaab` add preflight and hardening rules the rollout role
shares with I6). Supersedes the uncommitted draft `I7-859b27b.md` (its raw log
`I7-raw/mutants-all.log` is committed as the `859b27b4` record). Task-local key `i7`
(PG 57526): **not used** - I7 touches no SQL/RPC and D9/L3 are not on the base; the exercise
runs over R2's merged controller with R2's D9/L3 fakes.

## Changed paths (all owned)

* `apps/infrx-api/deploy/lab/rollout/infrx-lab-rollout.service` - I6's shape (bounded; OFF
  until I2L's `/etc/infrx-lab/enabled` AND `/etc/infrx-lab/rollout.env`; `python3 -I`
  preflight `--role rollout`; egress denied; `--pull never`; uid/gid 10003:10003; no consumer
  coupling), readiness port 8016, nothing mounted, no capability added, `Restart=on-failure`
  (a failed pass restarts and converges).
* `infra/lab/workers/rollout/RUNBOOK.md` - entry-point contract (pass loop, `/readyz` = DB +
  last-pass age, `emergency-rollback` subcommand), safety model (never expands on its own;
  admission independent of it; pins and balances out of its reach; no capacity purchases),
  enable / emergency controls, observability and alerts (verdicts, cohort counts, evidence
  age vs `max_lag_s`, alias convergence, StartLimit exhaustion), rollback exercise (local vs
  owed), I7.c staging/load environment and the measured hardware matrix (only Marlin-2B on
  g6e L40S is measured; every other variant stays unregistered or `inconclusive`).
* `apps/infrx-api/tests/i/lab_rollout/` - `test_units.py` (2 cases; the capacity case now
  also runs I6's exact-argv `hardening`), `test_exercise.py` (1), `mutants.py` +
  `test_mutants.py` (own process). The rollout role's preflight refusals (adapter, budget,
  `AWS_*`, `LD_PRELOAD`) are cases of `tests/i/lab_pipeline/test_preflight.py`.

## Commands (from `apps/infrx-api` unless noted)

| # | command | exit | result |
|---|---|---|---|
| 1 | fail-first (ef7f2c3a) `pytest -q tests/i/lab_rollout` (unit and runbook absent) | 1 | 2 failed (`FileNotFoundError`), 1 passed: the exercise proves the packaged contract over merged R2 and is armed by the R2 mutants (`I7-raw/seam-red.log`) |
| 2 | fail-first (security lens) - rollout unit cases in `I6-raw/seam-red-lens.log` | 1 | both I7 unit cases failed (`--user 10003:10003`, `--pull`) |
| 3 | `pytest -q tests/i/lab_pipeline tests/i/lab_rollout` | 0 | 47 passed, 1 skipped |
| 4 | `INFRX_MUTANTS=all pytest -q tests/i/lab_rollout/test_mutants.py` (with I6's, one run) | 0 | **15 I7 mutants killed** (12 on the unit, 3 on R2 under the exercise), 0 survivors (`I6-raw/mutants-all-af0ceaab.log`, 77 passed together) |
| 5 | `pytest -q tests/i/lab_eval/... tests/r/control` | 0 | 44 passed |
| 6 | ruff (as I6 row 8) | 0 | clean |
| 7 | root: `INFRX_D_TASK=i6 make api-test` at af0ceaab (both tasks, one run) | 2 | as I6 row 9: only the 7 shared-lock `test_outbox_relay[valkey]` cases fail (foreign `infrx-d2-valkey`), both runs recorded in `I6-raw/api-test-af0ceaab.log` |

## Exercise results (I7.b, ROLLOUT-RECOVER)

Over R2's `Controller` (each pass a fresh instance = the unit's restart) and R2's fakes:
controller outage with stale telemetry -> `hold` (`metrics_stale`), approval refused, nothing
moves; a breach during a serving partition -> one D9 decision (`error_rate`), the pass fails,
a restart still partitioned fails and moves nothing; partition healed -> the next pass
converges the alias to the baseline; recovered metrics and an accepting report never bring the
candidate back; `emergency_rollback` on the rolled-back release records no second decision;
the only port calls were `release`, `transition` (2), `serving`, `rollback`.

## Local vs owed from staging (P-08)

Local: unit shape/hardening as shipped, the five-step exercise over R2 with fakes. Owed: the
same five steps on real D9 (lab-sql), L3 and R1; `kill -9` between the D9 decision and the
alias CAS; admitted jobs finishing on their pins and consumer balances equal before/after
(E4 regression with the unit stopped and started); a staging/load environment and any
hardware row beyond the measured one.

## Wiring requests

* **WR-I6-1** (shared): `Makefile:21` api-mutants appends `tests/i/lab_rollout/test_mutants.py`.
* **WR-I6-2** (shared): `pool_budget.py` `ROLES` gains `"rollout": 1`.
* **WR-I7-1** composition (`python -m infrx.lab.workers rollout`): the pass loop and the
  `emergency-rollback --policy-ref --reason` subcommand of RUNBOOK §1 over R2's `Controller`,
  D9 and L3 ports; `/readyz` = DB + last pass within 2 x interval; a pass exception exits
  non-zero. Proof: `test_exercise.py` rerun through the entry point on real D9/L3.

## Open issues

* Pin and balance preservation is shown structurally (the controller's only ports are D9's
  row and L3's alias); the end-to-end proof is E7L's / staging's.
* `StartLimitBurst=5` in 10 min: a long partition leaves the unit `failed` (alerted;
  runbook `reset-failed`). Accepted: an unbounded restart loop is I5's rejected shape.

## Estimate (remaining to merge)

optimistic 1 h / likely 2 h / pessimistic 4 h, confidence medium. Basis: one review round
(I5 analogue) on a small diff; the real D9/L3 rerun and staging are outside this task.

## Fix round (2026-09-28, findings 0-LW-1, 1-LW5-LW-1, 1-LW5-LW-2)

The rollout unit shares I6's preflight, so the I6 fix covers it unchanged (see
`I6-af0ceaa.md` "Fix round"): the continuation repro that hid `INFRX_IMAGE=--privileged` is
run with `--role rollout` under the host's systemd and refused; the preflight compares every
setting with the unit's own `EnvironmentFile=` view. Runbook: the manual preflight runs under
`systemd-run`; `LAB_EGRESS_ALLOW` may name `169.254.169.254` for instance-role S3 credentials
(staging proof owed, P-08). Unit and I7 tests unchanged; `INFRX_D_TASK=i7 INFRX_MUTANTS=all
pytest -q tests/i/lab_rollout/test_mutants.py` -> 17 passed, 15 mutants killed, exit 0
(`I7-raw/mutants-all-1ed86c80.log`).

Rulings: the rollout unit is covered by R185 (local content-addressed image, `--pull never`, enable marker and the role's own 0600 env file) and its shared I6 proposal by R184, both numbered in `08-contracts-v1-encoding.md` §10 at the lab-workers merge (2026-09-28, `codex/w5-merge-12`).
