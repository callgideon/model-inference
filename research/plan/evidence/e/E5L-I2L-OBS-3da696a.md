# I2L-OBS — Lab observe packaging (the I2L extension, 09:145) — lane lab-observe (LW5)

- Branch `codex/w5-lab-observe`, base `9a48300c`, code commit `fadd776b` (unchanged through head `3da696a0`).
- Filed under `e/E5L-*` because that is the lane's only owned evidence path (track I would be `i/I2L-OBS-*`; deviation).
- Nothing installed, enabled or touched on a host, AWS, S3, Supabase or Vercel. Every role is OFF.

## Changed paths

- `apps/infrx-api/deploy/lab/observe/infrx-lab-judge.service` — J2 judge worker (`python -m infrx.lab.workers judge`,
  WR-OBS-1), `ConditionPathExists=/etc/infrx-lab/judge.env`, own env file, uid 10004, read-only, cap-drop ALL,
  512 MiB / 0.5 CPU / cpu-shares 256 / 64 pids, health 8014, `docker stop -t 60` < `TimeoutStopSec=90`, 5 starts/10 min.
- `apps/infrx-api/deploy/lab/observe/infrx-lab-trace-gauges.{service,timer}` — oneshot every 60 s running
  `infra/lab/observe/trace_gauges.py` from the pinned monitor copy (`/opt/infrx/observe`, read-only mount), spool dir
  read-only, textfile `/var/lib/infrx/metrics/lab-traces.prom`; OFF unless `/etc/infrx-lab/traces.env`.
- `infra/lab/observe/observe.json` — names only: roles, env names with exposure, **storage grants** (both roles on
  `S3_TRACE_BUCKET` under `${OBJECT_PREFIX}trace/` only; pumps Get/Put/Delete, judge Get), **egress budgets** (judge:
  loopback hosts = J2's `LOCAL_HOSTS`, `JUDGE_MODE=dry_run`, `JUDGE_LIVE_BUDGET_USD=0`, approval P-10; traces: its own
  stores), `enabled: false`.
- `infra/lab/observe/alerts.json` — WR-T-5: T3's `retention.RULES` verbatim (TraceDeletionBacklogOld,
  FeedbackProjectionLagging, TraceLossHigh, TraceSpoolFilling) + runbook anchors.
- `infra/lab/observe/trace_gauges.py` — exporter: `retention.gauges` (ClickHouse) + feedback outbox lag (PostgreSQL) +
  sealed-segment bytes; atomic textfile with `infrx_trace_gauges_up`; a failure writes `up 0`, prints the error type only,
  exits 1.
- `infra/lab/observe/README.md` — roles, grants, egress, enable/disable, the four alarm anchors, drills.
- `tests/integration/lab_observe/test_i2l_obs.py` — 10 cases.

Design choice (deviation, one line): the trace *pumps* stay where composition WR-T-4 put them (consumer worker,
`TRACE_PUMPS`, off); the Lab trace role only measures them — a second pump process would contradict WR-COMP-4's
one-writer rule.

## Commands

| # | Command | Head | Exit | Result |
|---|---|---|---|---|
| 1 | `apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/lab_observe/test_i2l_obs.py` (tests first) | base + test | 1 | **RED 10 failed** (`E5L-raw-3da696a/i2l-obs-red.log`) |
| 2 | same after implementation | fadd776b | 0 | 10 passed |
| 3 | layer-1 mutants inside `INFRX_MUTANTS=all … tests/integration/lab_observe/test_mutants.py` | 3da696a0 | see E5L #9 | **22/22 I2L-OBS mutants killed** (`E5L-raw-3da696a/mutants-all.log`) |
| 4 | `cd apps/infrx-api && uv run --frozen pytest -q tests/i` (the App units, install, packaging, observe) alone | 3da696a0 | 0 | 273 passed, 5 skipped, 1 xfailed (the first run's 51 failures were all the i8 lock, E5L #10) |
| 5 | WR-OBS-2 patch check on a scratch copy | — | 0 | see E5L #13 |

## Cases → mutants

| Case | Oracle | Mutants |
|---|---|---|
| `every_observe_unit_is_off_until_its_role_env_file_exists` | OFF by default | `judge_unit_runs_without_its_env_file`, `gauges_timer_unconditional`, `manifest_enabled` |
| `no_app_unit_links_to_an_observe_unit_or_the_reverse` | Lab outage never stops inference | `judge_ordered_after_the_worker` |
| `each_unit_is_bounded_least_privilege_and_its_own` | bounded, own uid/env, read-only mounts | `judge_full_cpu_weight`, `judge_in_the_runtime_uid`, `gauges_writable_exporter_mount`, `gauges_reads_the_gateway_env` |
| `the_judge_worker_runs_its_role_and_drains` | role, health port, stop > drain | `judge_runs_another_role`, `judge_stop_shorter_than_the_drain` |
| `the_trace_gauges_run_on_a_timer_from_the_shipped_exporter` | shipped code, the textfile read | `gauges_write_another_textfile`, `gauges_writable_exporter_mount` |
| `secret_names_only_and_every_name_a_unit_reads_is_declared` | names only, all declared | `spool_dir_undeclared` |
| `storage_grants_are_object_prefixes_under_the_trace_root_never_a_bucket` | prefix grants, judge read-only | `judge_bucket_wide`, `judge_may_write` |
| `judge_egress_is_the_local_fake_and_a_zero_budget_until_p10` | no unapproved host or budget | `judge_egress_to_another_host`, `judge_live_budget` |
| `the_alarms_are_t3s_rules_with_runbook_anchors_and_no_name_clash` | T3's rules exactly | `alarm_threshold_drift`, `alarm_without_its_runbook` |
| `the_exporter_writes_t3s_gauges_and_a_failure_is_up_0_never_silence` | gauges, up 0, no secret printed | `exporter_silent_on_failure`, `exporter_prints_the_error`, `exporter_counts_every_file`, `exporter_up_always_1` |

## Wiring requests

WR-OBS-1 (judge entry point), WR-OBS-2 (observe cycle merges the Lab rules/textfile; exact diff), WR-OBS-4 (Makefile) —
text in `E5L-3da696a.md`.

## Owed from staging (P-08), not claimed

`systemctl show` of the installed units; the IAM policies from `storage_grants`; a host egress rule (I6 owns the Lab
default-deny); the evaluator firing on a seeded `lab-traces.prom`; a stopped ClickHouse on the box writing `up 0`.

## Estimate

Remaining for I2L-OBS: optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium; basis: one verify round
(I2L took one fix round, 1-LO-1) on 5 small files.

## Fix round (2026-09-28, code head 8e3e8e21)

Detail and commands: `E5L-3da696a.md` § Fix round. I2L-OBS changes: judge health port 8014 → **8017** (8014-8016 are
lab-workers' annotation/training/rollout); `alerts.json` gains the Lab-only `TraceGaugesDown` (`infrx_trace_gauges_up < 1`,
page) so an exporter failure pages; README §4 adds the pin step (WR-OBS-5) and §6 the `TraceGaugesDown` runbook;
WR-OBS-2 revised (Lab rules merged only when pinned) and WR-OBS-5 filed (`E5L-wiring/`). test_i2l_obs: 13 cases (3 new),
42/42 layer-1 mutants killed (corrected at merge 9 from 41/41 — 1-LO-EVID-2; 38 at 3da696a0).
