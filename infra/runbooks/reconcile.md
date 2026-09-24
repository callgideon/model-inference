# Reconcile — money, unknown usage, unsettleable jobs

Conventions: [README.md](README.md). The invariant every I3B drill ends with
(`recoverykit.World.reconcile()`): no accepted job lost, pins unchanged, holds equal to what
is still held, one terminal usage projection per job (plus one when an unknown-usage hold is
released), a debit only when settled and then exactly the frozen price snapshot's debit of
the terminal usage, and per tenant `ledger = granted - settled debits`,
`reserved = holds still held`, `available >= 0`. On PostgreSQL the same thing is two
detector views (R59-7), and nothing here edits money: wallet totals move only through the
ledger trigger.

## Drift

`ReconciliationDrift` (a detector row with non-zero drift). **Page.** As `service_role`
through the pooler, read-only:

```sql
set default_transaction_read_only = on;
select * from infrx.wallet_reconciliation        where ledger_drift <> 0 or reserved_drift <> 0;
select * from infrx.credit_wallet_reconciliation where ledger_drift <> 0 or reserved_drift <> 0;
-- accepted jobs that are neither terminal nor making progress
select state, count(*) from infrx.jobs where state not in ('succeeded','failed','cancelled','expired') group by 1;
```

1. [Maintenance](rollback.md#maintenance) first if a wallet is drifting while admission is on:
   a wrong `reserved_total` admits or refuses the wrong work.
2. Do **not** `update` a wallet, hold or job. The correction is D5's reconciliation
   operation (an audited ledger entry, `operator_adjustment` / `admin_reconcile`), **PENDING
   D5**; until it exists, record the drift rows in the session record and escalate to D.
3. After the correction the detectors return no rows and `infrx_reconciliation_drift` is 0.

After any recovery drill or incident, the same queries are the "nothing was lost" check.

## Stale

`ReconciliationStale`: no clean pass for over an hour - the reconciler is not running
(Q3/D5's timer, **PENDING**), or every pass finds drift (see [Drift](#drift)).

## Unsettleable

`UnsettleableJobs`: the reaper found overdue jobs it could not settle (the reference store
records why, e.g. no journal capacity for the terminal event); their holds stay reserved
until it can. Free the constraint (journal capacity: expired chunks are pruned by
`StreamStore.expire`), then let the reaper run again; the gauge returns to 0.

## Unknown usage

`UnknownUsageBacklog`: holds waiting for unknown-usage reconciliation. Each one is a job that
published output without authoritative usage (`lost_after_publication`, `engine_incomplete`);
the reaper releases it as platform-absorbed after `UNKNOWN_USAGE_RECONCILE_S` (24 h), never
as a late customer debit (`rc01` drills the release). A growing backlog means the engine
stopped reporting usage or workers keep dying after publishing: [restart.md](restart.md#engine).

Counting what the reaper returned: `JobStore.recover()` returns that release as a
`TerminalOutcome` of a job that was **already terminal**, indistinguishable by type from a
new terminalization (I3B Finding 3; a distinct record is requested from D3/D5). Until then
tell them apart by `settled_at`: the release keeps the job's ORIGINAL `settled_at`, so an
outcome with `settled_at <= now - UNKNOWN_USAGE_RECONCILE_S` is a release - pass its job id
in `metrics.record_recovery(reg, produced, released=...)` - and never a second terminal job
(counting it twice reports `infrx_jobs_terminal_total{failed,lost_after_publication} = 2`
for one job). A heuristic with a known ceiling: it relies on the reference store's behaviour
and goes when D3/D5 return the release as its own record.

## Sanitizer

`MetricsSanitizerRejections`: a call site tried to write an undeclared label value (a prompt,
a URL, an error message, an unknown code) or a bad number into a metric; it was replaced by
`other` or dropped, so nothing leaked. It is a code defect, not an operating condition: the
`family` label names the metric; file it against the track that emits it.


## From the coordinator host

`apps/infrx-api/.venv/bin/python infra/runbooks/drift.py [--hours N]` runs the three drift queries above as one `SET TRANSACTION READ ONLY` transaction on the pooler's transaction port (6543) and prints counts only; the password comes from SSM into the process. Use 6543, not 5432: the pilot runtime's pools hold every one of the session pooler's 15 slots (`EMAXCONNSESSION`), so a session-mode operator connection is refused while the pilot runs (2026-09-24).

## Verification log

- 2026-09-22 (I3B.c): Written from the drills' `reconcile()` and the detector views of
  0003/0006 (read on E2's stack by `bk01`-`bk04`). The reconciler and the correction
  operation are PENDING Q3/D5. Nothing run on hosted.
- 2026-09-23 (I3B fix round, D1/D5): the invariant names the debit amount (drilled by
  `reconcile()`); the `settled_at` rule for telling a released hold from a new terminal job
  is written down until D3/D5 return a distinct record.
- 2026-09-24 (coordinator): `drift.py` added (host-side read-only aggregates; transaction pooler); the session pooler is full while the pilot runs.
