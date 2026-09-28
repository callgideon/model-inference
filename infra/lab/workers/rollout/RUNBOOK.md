# Lab rollout controller (I7) - runbook, OFF by default

Scope: the R2 controller of LAB-M4 as its own process and systemd unit, I6's shape
(`infra/lab/workers/training/RUNBOOK.md`: bounds, OFF until its env file exists, the
preflight gate, egress denied, the hardening and residuals of its §2, no consumer coupling)
and I2L's Lab-wide enable marker `/etc/infrx-lab/enabled`.

| role | work | unit | health port | adapter |
|---|---|---|---|---|
| `rollout` | R2 guardrail passes, CAS rollback, operator emergency rollback | `apps/infrx-api/deploy/lab/rollout/infrx-lab-rollout.service` | 8016 | none (no spend) |

**Default: OFF.** Nothing here runs against staging or the pilot box before the operator
allocates a Lab window (P-08); R1's admission hook (`ROLLOUT_ROUTING`) is off on the launched
API, so no release exists for the controller to act on until then.

## 1. Entry point (the composition batch)

`python -m infrx.lab.workers rollout`: every interval (the entry point's constant, 30 s) one pass
calls R2's `Controller.step` for each D9 release in `running` or `rolled_back`, with R1's
aggregates as `Live` and the stored B2 report. `/livez`; `/readyz` = the database answers and
the last pass finished within 2 x the interval (the controller's own readiness: the gateway's
`/readyz` never depends on it); `/metrics`. A pass that raises (a partitioned serving control,
a lost CAS race three times) exits non-zero: `Restart=on-failure` restarts it and the next
pass converges. `python -m infrx.lab.workers rollout emergency-rollback --policy-ref <ref>
--reason <text>` (operator id from `LAB_OPERATOR_ID` of the invoking shell) runs R2's
`emergency_rollback` once and exits.

## 2. Safety model (what losing the controller means)

* **It never expands on its own.** Expansion is an operator `approve` on an `expand` verdict
  (R2); a pass only holds or rolls back. A controller outage, a partition or stale telemetry
  therefore freezes traffic at the current stored policy: stale metrics are a `hold`
  (`metrics_stale`) and an approval on them is refused.
* **Admission does not depend on it.** R1 reads D9's current policy at admission; a
  release-store failure is a retryable 503, never a silent move to the baseline. Admitted jobs
  keep the serving and rate pins they were admitted with (R1/L3), and settlement and balances
  are the consumer's, untouched by any rollback: the controller's only ports are D9's release
  row and L3's serving alias.
* **A rollback is decided once and converges.** D9's CAS records one decision; the alias CAS
  follows. Killed between the two, the next pass converges; an alias someone else moved on is
  left alone.
* **No automatic capacity purchases.** No adapter, budget or cloud credential in its env file
  (the preflight refuses them), nothing mounted, no capability added, `--pull never` (the image
is the release's local id; the daemon never fetches one).

## 3. Enable, disable, emergency controls

Enable (window only): write `/etc/infrx-lab/rollout.env` (ubuntu, 0600: `INFRX_IMAGE`,
`LAB_DATABASE_URL` on the transaction pooler, `LAB_ROLLOUT_CONCURRENCY=1`, optional
`LAB_EGRESS_ALLOW` naming nothing but the object store and, for the instance-role
credentials, `169.254.169.254`), run the preflight under systemd's reading of the file, as the
unit does (`sudo systemd-run --wait --pipe -q --uid ubuntu -p EnvironmentFile=/etc/infrx-lab/rollout.env /usr/bin/python3 -I /home/ubuntu/model-inference/infra/lab/workers/training/preflight.py --role rollout --env-file /etc/infrx-lab/rollout.env` prints `PASS rollout`; a
plain `python3 preflight.py` refuses, since its environment is not the unit's), install the unit, make sure I2L's marker
`/etc/infrx-lab/enabled` exists, `enable --now` the unit, then
`curl -fsS 127.0.0.1:8016/readyz`.

Emergency controls, in order:
1. **Roll one release back**: `emergency-rollback` above (no evidence needed, any live state,
   once; a repeat only converges).
2. **Stop the controller**: `sudo systemctl disable --now infrx-lab-rollout`. Traffic stays
   at the current stored policy; nothing expands.
3. **Stop routing**: turn `ROLLOUT_ROUTING` off in the gateway (coordinator; E4 regression
   first). Every request is then served as before the Lab.

## 4. Observability and alerts (Lab only, never paged as a consumer outage)

`infrx_lab_rollout_verdicts_total{policy,action,reason}` (hold reasons include
`metrics_stale`, `min_requests`, `quality_coverage`, `cohort_skew`, `before_horizon`,
`no_report`), `infrx_lab_rollout_cohort_requests{policy,arm}` (R1's counts: no org, user or
key), `infrx_lab_rollout_evidence_age_seconds{policy}` (now - `observed_until`, against the
plan's `max_lag_s`), `infrx_lab_rollout_alias_converged{policy}`.
Alerts: a `rolled_back` release whose alias is not the baseline for more than 2 passes; the
unit in `failed` (StartLimitBurst=5 in 10 min exhausted: a persistent partition - run
`systemctl reset-failed infrx-lab-rollout && systemctl start infrx-lab-rollout` once the
serving control answers); evidence age over `max_lag_s` for a running release.

## 5. Rollback exercise

Local (task-local key `i7`, no container needed): from `apps/infrx-api`,
`uv run --frozen pytest -q tests/i/lab_rollout` - R2's real controller, D9/L3 fakes:

1. controller outage with stale telemetry: a restarted controller holds, approval refused;
2. a breach during a serving-control partition: one D9 decision, the pass fails, a restart
   while still partitioned fails too, nothing moves;
3. the partition heals: the next restart converges the alias to the baseline; recovered
   metrics and a winning report never bring the candidate back;
4. `emergency-rollback` on the rolled-back release only converges (no second decision);
5. the controller called nothing but D9's release row and L3's alias.

Staging (P-08 window, owed): the same five steps on the allocated Lab environment with real
D9 (lab-sql), L3 and R1, plus: `kill -9` of the running unit between the D9 decision and the
alias CAS (journal + `infrx_lab_rollout_alias_converged`); admitted jobs finish on their pins
and the consumer balances before and after are equal (E4 regression with the unit stopped and
started).

## 6. Staging, load environment and the hardware matrix (I7.c)

The staging/load environment is not allocated (P-08). The only serving hardware this repo has
measured is Marlin-2B on the g6e.2xlarge dev box (L40S; `models/marlin2b/README.md`); every
other (model, GPU) pair in `research/matrix/pairs.json` is an estimate. R3 registers a variant
only through a W3 engine adapter (only `vllm` has one) and claims an optimization only with
load measurements from an experiment branch's `results/`; so every variant other than a vLLM
serving of Marlin-2B on that hardware stays unregistered or `inconclusive`, and no release can
route to it. A new hardware row needs a measured `results/` commit first.

## Verification log

- 2026-09-28: I7 packaging and the local rollback exercise written (lab-workers lane, LW5);
  I2L's enable marker, `python3 -I`, `--pull never` and group 10003 added. Nothing run on
  staging or the pilot box.
- 2026-09-28 (fix round): the preflight refuses a file systemd and docker read differently
  and compares with the unit's own environment; the manual preflight runs under
  `systemd-run`; `169.254.169.254` may be allowlisted for the instance role (training
  runbook §2). Local only.
