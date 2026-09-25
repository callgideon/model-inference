# W5-MERGE — codex/w5-merge (2026-09-25)

Base `ccf37b55` (codex/m5-merge). Head at time of checks `09d83a97`. Task-local only: `INFRX_D_TASK=w5`
(postgres 55445, valkey 55491), MinIO `infrx-w5-s3` on 127.0.0.1:55497
(`pgsty/minio@sha256:b6bfe723…`, `--pull never`, E2 local literals, bucket `infrx-w5`); the container was removed
when the checks finished. Nothing was pushed. claude/consumer-v1 (ec21811b) was NOT merged, so the G8 straddle case was deselected.

## Commits
| SHA | What |
|---|---|
| `c58e37d0` | merge --no-ff codex/w5-readiness (25592682) |
| `fa446ae7` | W5 wiring 1 (patch `W5-wiring-27afab7.patch`; `__main__.py` hunk hand-reconciled with I8 `pool=pool`) + Makefile api-mutants += test_w5_mutants |
| `7b663206` | merge --no-ff codex/g7-merge (739c0414): clean, no conflicts |
| `09d83a97` | tests/w/test_w5: the three over_the_cap cases admit under a pinned 120 s profile (`WAS`), because G7 WR-1 moved the default to 82 s |

## Conflict resolutions (w5-readiness merge)
- These 11 F2C cherry-picked paths were resolved to ours: types.ts, apps/app/tests/contracts/mutants.json,
  conformance/{acceptance,lifecycle}.py, fixtures/acceptance/lifecycle.json,
  fixtures/v2/result_read_cases.json, contracts/v2/__init__.py (ours is already the union),
  contracts/v2/lifecycle.py, tests/contracts/mutants.py, 01-contracts.md, 02-durable-protocols.md.
- contracts/tasklocal.py was resolved to ours: theirs is byte-identical to ancestor 2efdeebd, and ours adds the I8 pgbouncer port 55496.
- worker/service.py: the two sides' import lines were unioned (`record_pool, record_reconciliation`), and the `pool=` and `reconciliation=` fields are both kept.

## Commands (serial; an earlier parallel attempt collided on the single w5 postgres and was discarded)
| Command | Exit | Result |
|---|---|---|
| PART A `pytest -q -rxXs tests/w` (no MinIO) | 0 | 301 passed, 10 skipped (7 S3, 3 mutant-list placeholders) |
| PART A `INFRX_MUTANTS=all` w5/prep_worker/worker_main mutants (no MinIO) | 0 | 133 passed, 12 skipped (S3) |
| PART A `pytest -q tests/contracts -k 'not pg'` | 0 | 1260 passed, 6 deselected |
| `pytest -q tests/g tests/m tests/contracts -p no:cacheprovider --deselect …test_credit_cutover__an_admission_open_across_the_freeze_is_waited_for` (on 7b663206) | 0 | 2422 passed, 1 skipped, 1 deselected |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |
| `pytest -q -rxXs tests/w` + MinIO (on 7b663206) | 1 | 10 failed, 298 passed: 5 were the over_the_cap regressions (fixed in 09d83a97), 5 are the PG round trips below |
| `pytest -q -rxXs tests/w` + MinIO (on 09d83a97) | 1 | **5 failed, 303 passed, 3 skipped** |
| `INFRX_MUTANTS=all` w5/prep_worker/worker_main mutants + MinIO (on 09d83a97) | 1 | **12 failed, 133 passed**: all 12 PG mutants report `broken_runner` because the pristine tree fails those 5 cases. There were no survivors, and test_w5_mutants passes in full |

## Blocker (unresolved): the gateway never calls `admit_ready`
Five cases fail the same way:
- `test_prep_worker_pg__the_worker_process_prepares_and_runs_an_admitted_job`
- `test_prep_worker_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it`
- `test_prep_worker_pg__a_tokenize_count_that_disagrees_with_the_usage_settles_at_the_usage`
- `test_worker_main_pg__a_job_the_gateway_admitted_runs_in_the_worker_process`
- `test_worker_main_pg__sigterm_drains_the_in_flight_job_and_exits_0`

In each one the worker log shows `preparation of <id> not claimed: not_claimable`.

Cause: `infrx/gateway/routes/relay.py` still admits through `self.jobs.admit` / `admit_credit`, which writes no
execution-ready marker. W5's barrier (wiring 1: `readiness=PgLifecycle`) then correctly refuses to prepare the job.
This is W5 wiring request 4 (evidence W5-27afab7.md §wiring 4): "G7 (gateway admission) must call `admit_ready`". The G7 tip 739c0414 does not do it,
and neither does claude/consumer-v1 (`git log -S admit_ready -- apps/infrx-api/infrx/gateway` finds nothing). `PgLifecycle.admit_ready`
exists (infrx/state/lifecycle.py:178). The gateway relay is outside this lane, so it was not changed here.
Consequence: merging this branch as it stands stops every PostgreSQL job from being prepared. Do NOT deploy it without that wiring.

## Estimate
Remaining after the admit_ready wiring lands: rerun tests/w plus the 3 mutant lists with MinIO, about 35 min serial.
Optimistic 0.5 h, likely 1.5 h, pessimistic 4 h. Confidence medium. Basis: 5 PG cases and 12 PG mutants fail only on the missing marker.
