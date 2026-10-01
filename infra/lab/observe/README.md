# Lab observe runbook — trace gauges, the judge worker, grants, egress, alarms (I2L-OBS)

**Disabled by default — still disabled on 2026-10-01** (the Lab control service is ON on the box, the observe units are installed inert; runbook 08's state block). **Operator-run; not yet run.** The lab-observe lane wrote this runbook,
the units it installs (`apps/infrx-api/deploy/lab/observe/`), the names manifest
([`observe.json`](observe.json)), the alarms ([`alerts.json`](alerts.json), T3's `RULES`) and the
exporter ([`trace_gauges.py`](trace_gauges.py)). Nothing here changed a host, AWS, S3, Supabase or
Vercel; nothing is a live-state claim. LAB-M1 enable needs **P-08** (the staging window), **P-09**
(capture consent) and, for any live judge, **P-10** (provider, rates, budget). Until then
`observe.json` keeps `"enabled": false` and no env file below exists.

Local proof: `tests/integration/lab_observe/test_i2l_obs.py` (packaging) and E5L's gate
(`tests/integration/lab_observe/runner.py`: capture → search → review → judge dry-run on the real
local services, with faults).

## 1. Roles

| Role | Unit | What it runs | Env file (0600, root) |
|---|---|---|---|
| `traces` | `infrx-lab-trace-gauges.service` + `.timer` (every 60 s, oneshot) | `trace_gauges.py`: T3 loss, deletion backlog, feedback lag, spool bytes → `/var/lib/infrx/metrics/lab-traces.prom` | `/etc/infrx-lab/traces.env` |
| `judge` | `infrx-lab-judge.service` (health `127.0.0.1:8017`; 8011-8016 are the eval, annotation, training and rollout workers') | `python -m infrx.lab.workers judge` (entry point: WR-OBS-1): J2 submit / reconcile / collect | `/etc/infrx-lab/judge.env` |

The trace **pumps** (ship, retention sweep, feedback projection) are not a Lab unit: they run in the
consumer worker behind `TRACE_PUMPS` (composition WR-T-4, off by default, never installer-settable),
and capture itself stays off in v1 (R140). This role only measures them. No consumer unit names a
Lab unit and no Lab unit orders itself after a consumer one: a Lab outage never touches inference.

## 2. Storage grants (object prefixes only)

`observe.json` `storage_grants`: both roles on `S3_TRACE_BUCKET` under `${OBJECT_PREFIX}trace/`
only (T2I's `CONTENT_PREFIX`); the pumps get Get/Put/Delete, the judge Get only. Neither touches
`media/`, `uploads/`, `payloads/` (the App's) or `lab/` (I5's). [OP] one IAM policy per role
from those rows, `Resource: arn:aws:s3:::<bucket>/<prefix>*`, never `<bucket>/*`.

## 3. Egress budgets

- judge: loopback only (J2's `HttpJudgeProvider` refuses any other host), `JUDGE_MODE=dry_run`,
  `JUDGE_LIVE_BUDGET_USD=0` until P-10. PROVIDER_USD with a named payer; never a CREDIT wallet.
  ⚠️ TO BE VERIFIED: a host egress rule (the unit uses the host network, as I5's); I6 owns the
  default-deny egress rule for Lab workers.
- traces: its own stores only (`CLICKHOUSE_URL`, `S3_ENDPOINT_URL`, `LAB_DATABASE_URL`).

## 4. Enable one role (staging window only) [OP]

1. Budget first: `python infra/lab/workers/eval/pool_budget.py --consumer-env-file /etc/marlin2b-gateway.env --lab-env-dir /etc/infrx-lab`.
2. Write `/etc/infrx-lab/<role>.env` (root 0600) with the names in `observe.json`; secrets only there.
3. Pin the Lab files into the monitor's copy: re-run `infra/rollout/steps/72-observe-install.sh` at the
   release (WR-OBS-5 copies `infra/lab/observe` into `/opt/infrx/observe`), then
   `test -f /opt/infrx/observe/infra/lab/observe/alerts.json && test -f /opt/infrx/observe/infra/lab/observe/trace_gauges.py`.
   The gauges unit mounts that directory and the observe cycle (WR-OBS-2) reads its alarms there;
   a pin without them keeps the App's rules (the Lab file is merged only when present) and the
   missing textfile pages as `ScrapeFailed`.
4. `sudo cp apps/infrx-api/deploy/lab/observe/infrx-lab-<unit> /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now <the .timer or the judge .service>`.
5. Verify: `cat /var/lib/infrx/metrics/lab-traces.prom` shows `infrx_trace_gauges_up 1`; or
   `curl -fsS 127.0.0.1:8017/readyz`; the consumer checks (`infra/rollout/steps/60-verify-local.sh`) unchanged.

## 5. Disable / roll back

`sudo systemctl disable --now <unit> && sudo rm /etc/infrx-lab/<role>.env`. Nothing is lost: the
gauges are recomputed from the stores; a judge run interrupted mid-submit stays `submitting` or
`ambiguous` in D6J with its hold kept and is reconciled by its submit key, never sent twice.

## 6. Alarms (`alerts.json`, T3's `RULES` + `TraceGaugesDown`; evaluated once WR-OBS-2 and WR-OBS-5 merge)

### TraceDeletionBacklogOld

A deletion or expiry nothing holds has waited > 24 h for physical cleanup (reads already hide it).
Check the retention pump (`TRACE_PUMPS` worker logs, `trace_retention` step); `sweep()` failures
stay pending and retry. A held tombstone is excluded (`infrx_trace_deletion_held`).

### FeedbackProjectionLagging

Accepted feedback (PostgreSQL, unaffected) has waited > 15 min for the ClickHouse projection. Check
ClickHouse reachability and the `feedback_projection` step; events are redelivered, never lost.

### TraceLossHigh

More than 1 % of eligible traces carry a loss reason. Check `infrx_trace_capture_dropped` and the
spool (budget or disk); inference is never slowed to capture (TRACE-BOUNDS).

### TraceSpoolFilling

The spool holds > 80 % of `TRACE_SPOOL_MAX_BYTES`: capture will pause. The shipper is not keeping
up (ClickHouse or S3 down, or held segments): restore the store; sealed segments ship on the next pass.

### TraceGaugesDown

The exporter wrote `infrx_trace_gauges_up 0`: it could not read ClickHouse, the feedback outbox or
the spool (its stderr names only the error's type: `journalctl -u infrx-lab-trace-gauges`). Every
trace alarm above is blind until it recovers. Check `CLICKHOUSE_URL` / `LAB_DATABASE_URL`
reachability from the box and the spool mount; the next timer run rewrites the file.

## 7. Drills

| Drill | Local evidence | Owed from staging (P-08) |
|---|---|---|
| units off by default, bounded, no App coupling | `test_i2l_obs.py` | `systemctl show` of each installed unit |
| alarms = T3's rules, runbook anchors | `test_i2l_obs.py` | the evaluator firing on a seeded textfile |
| exporter up/down | `test_i2l_obs.py` (a failing store writes `up 0`; `TraceGaugesDown` fires through the evaluator) | a stopped ClickHouse on the box |
| capture → search → review → judge dry-run, faults, no App outage | E5L `runner.py` (`research/plan/evidence/e/E5L-*`) | the same with the staging stores |
