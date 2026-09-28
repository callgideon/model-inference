# Lab dataset, evaluation and checkpoint workers (I5) - runbook, OFF by default

Scope: the three Lab worker roles of LAB-M2, each its own process and systemd unit:

| role | work | unit | health port | concurrency knob (default) |
|---|---|---|---|---|
| `datasets` | N1 imports, N2 versions and exports (long jobs, never in a frontend) | `apps/infrx-api/deploy/lab/eval/infrx-lab-datasets.service` | 8011 | `LAB_DATASETS_CONCURRENCY` (2) |
| `eval` | B1 runs: the `eval_run` Lab outbox events | `.../infrx-lab-eval.service` | 8012 | `LAB_EVAL_CONCURRENCY` (4) |
| `checkpoints` | B3: the `checkpoint_received` Lab outbox events | `.../infrx-lab-checkpoints.service` | 8013 | `LAB_CHECKPOINTS_CONCURRENCY` (1) |

**Default: OFF.** A role runs only when `/etc/infrx-lab/<role>.env` exists
(`ConditionPathExists`); `install.sh` and the rollout never write it, never install these
units and never name them, and no consumer unit depends on them. Nothing here is executed
against staging or the pilot box until the operator allocates a Lab staging window (P-08);
this document is the procedure for that window.

## 1. The entry point (WR-B-5, the composition batch)

Each unit runs `python -m infrx.lab.workers <role>` in the release image. The entry point is
coordinator wiring, not part of I5; the units and this runbook fix its contract:

* reads only its env file: `LAB_DATABASE_URL` (the **transaction** pooler, port 6543),
  `LAB_<ROLE>_CONCURRENCY`, the object-store names (`LAB_S3_BUCKET`, `LAB_S3_ENDPOINT` when
  not AWS) and, for `eval`, the dev endpoint target (WR-B-3); exits 2 naming a missing
  setting, never printing a value;
* `eval`: `PgLabDataStore.dispatch_pending` -> `runner.resume` + `Runner.run` per `eval_run`
  event, `lab_recover` on a timer; `checkpoints`: `checkpoints.on_checkpoint` per
  `checkpoint_received` event (`CapacityExhausted` releases the event for later);
  `datasets`: the N1/N2 job queue;
* answers `/livez`, `/readyz` (database and object store reachable) and `/metrics` on
  `127.0.0.1:$LAB_WORKER_HEALTH_PORT` (set by the unit);
* SIGTERM stops leasing; in-flight attempts finish within 60 s or are left to expire (a lease
  is recovered by `lab_recover`, never committed stale: every write is fenced).

## 2. Enable one role (staging window only)

1. Budget first, on the box, with the file you are about to install:
   `python infra/lab/workers/eval/pool_budget.py --consumer-env-file /etc/marlin2b-gateway.env --lab-env-dir /etc/infrx-lab`
   (the new `<role>.env` staged in that directory). Any `FAIL` line = stop. The Lab has its
   own allotment (`--lab-limit`, 20 transaction-pooler clients); it never uses the session
   pooler, whose 15 slots are the consumer's.
2. Write `/etc/infrx-lab/<role>.env`, `root:root 0600`: `INFRX_IMAGE=<the release digest,
   as in the gateway env>`, `LAB_DATABASE_URL=<6543 DSN of the Lab role>`,
   `LAB_<ROLE>_CONCURRENCY=<n>`, object-store names. Secrets live only in this file (never in
   argv, logs or this repository); rotating a credential = rewriting it and restarting.
3. `sudo cp apps/infrx-api/deploy/lab/eval/infrx-lab-<role>.service /etc/systemd/system/ &&
   sudo systemctl daemon-reload && sudo systemctl enable --now infrx-lab-<role>`.
4. Verify: `curl -fsS 127.0.0.1:<port>/readyz`; the consumer's own checks
   (`infra/rollout/steps/60-verify-local.sh`) unchanged and green.

Metrics (the entry point exports; alerts are Lab-only and never page as a consumer outage):
`infrx_lab_worker_up{role}`, `infrx_lab_leases_expired_total`, `infrx_lab_outbox_pending`,
`infrx_lab_run_credit_spent{run}` against its budget, `infrx_lab_checkpoint_skips_total{reason}`.
Budget alerts: a run's recorded CREDIT at 80 % of its limit; a subscription's queued limits at
its total; the provider_dev wallet's 402s (B1 stops with `wallet_exhausted`).

## 3. Roll back / disable

`sudo systemctl disable --now infrx-lab-<role> && sudo rm /etc/infrx-lab/<role>.env`. Nothing
is lost: unfinished cases stay leased and expire back to pending, outbox events stay
unacknowledged and are redelivered when the role is enabled again. The consumer is untouched
by the stop (no unit is `PartOf=` or `Requires=` a Lab unit).

## 4. Drills

Local (task-local key `i5`: PostgreSQL 57523, MinIO 57524), run by the lane:
`INFRX_D_TASK=i5 uv run --frozen pytest -q tests/i/lab_eval` from `apps/infrx-api`.

| drill (LAB-WORKERS) | local evidence | owed from staging (P-08 window) |
|---|---|---|
| unit shape: bounds, OFF by default, no consumer coupling | `test_units.py` (every unit, install.sh, gateway/worker units) | `systemctl show` of each installed unit |
| separate pool budget | `test_pool_budget.py` (I8's rows unchanged, Lab allotment, session refusal) | the budget on the box's real env files; Supavisor `max_client_conn` read (I8's ⚠️) |
| worker restart with active leases; no stale commit | `test_drills_pg.py` on real D7 | kill -9 of the running unit mid-run; `infrx_lab_leases_expired_total` |
| database backup / restore | `test_drills_pg.py`: `pg_dump -Fc` / `pg_restore` of the Lab database, the run resumes | Supabase PITR restore of the Lab schema into a scratch project (I2L/P-08) |
| object store lost / restored | `test_drills_pg.py` on MinIO: loss is a visible `missing_content` with no paid call; restored, a rerun completes | S3 versioned-bucket restore |
| revoked credential | `test_drills_pg.py`: a 401 dev endpoint fails each case once, no retry | a rotated `LAB_DATABASE_URL` / endpoint key on the box |
| filled temp disk | the unit's bounded tmpfs (`test_units.py`); no Lab path spools to local disk | ENOSPC in `/tmp` of a running unit |
| Lab outage vs inference | no consumer unit depends on a Lab unit (`test_units.py`) | E4 regression with every Lab role stopped and started |
