# Rollback — a rollout, the maintenance switch

The rules are infra/README.md §8; this is the procedure. Conventions: [README.md](README.md).
Drills: `test_i3b_bk04` (the maintenance switch, real PostgreSQL), `test_i3b_rc10` (the
rollout rollback, **PENDING I2B**: it needs I2B's deploy/rollback scripts and fails the day
`apps/infrx-api/deploy/rollback*` exists, so the drill gets written then).

## Rollout rollback

Trigger: a deploy made things worse (alerts after the rollout, a failed post-deploy check).
The one rule that is not negotiable: **once CREDIT admission is enabled, the previous
runtime is only a rollback target if it meters.** The pre-refactor gateway does not reserve
or settle; rolling back onto it would serve work nobody pays for and nobody can reconcile.
If no compatible metered runtime exists, the answer is [maintenance](#maintenance), not the
old gateway.

1. **Log** the rollback (trigger, the deployed and target revisions - `infrx_build_info` -
   and the snapshot id of the pre-deploy state) in the session record.
2. **Pause admission**: [maintenance](#maintenance). Accepted jobs keep running.
3. **Drain and fence**: [restart.md](restart.md#drain). What cannot finish inside the drain
   bound is released to the store; the reaper requeues prepublication attempts after the
   lease TTL. Nothing is settled by the rollback itself.
4. **Deploy the previous compatible runtime** with I2B's script (**PENDING I2B**; its name
   and arguments go here when it lands). Schema changes are never rolled back to roll back
   code: 0003-0009 are additive and the older runtime runs on them (`bk02`, restore.md A7).
5. **Rebuild the index** from PostgreSQL ([index-loss.md](index-loss.md#index-loss)).
6. **Resume admission** only after `/readyz` is ready: [maintenance](#maintenance), exit.
7. **Reconcile** ([reconcile.md](reconcile.md#drift)) and append to the record: durable state
   before and after, and whether any accepted job changed state.

Box-level rollback (the whole root volume) is [restore.md](restore.md#box-snapshot); it also
discards everything written to the root volume since the snapshot, so it comes after this,
not instead of it.

Window: ⚠️ TO BE VERIFIED (P-18) - first measured by the coordinator's I2B rollout drill.

## Maintenance

Admission refused, everything else untouched: accepted jobs finish, settle and replay. On
PostgreSQL it is two feature flags (0006); every admission write then fails with SQLSTATE
`55000` and writes nothing, which the ingress maps to a maintenance `503` (D2's mapping).
It exists only once 0006 is applied - today's hosted project (0001-0002) has no flags, so
until the apply the only way to stop admission is to stop the gateway.

**[irreversible: none - but it is a hosted write]** Log it. As `service_role` (the flags are
the one table that role may update), through the pooler:

```sql
-- enter maintenance
update infrx.feature_flags set enabled = false, updated_by = 'i3b-drill', reason = 'maintenance drill' where name in ('legacy_usd_admission', 'credit_admission');
-- check: raises SQLSTATE 55000 while in maintenance
select infrx.require_feature('credit_admission');
-- leave maintenance: the same statement with `enabled = true`
```

Write your own name and reason into `updated_by`/`reason` (the drill's literals above are
what `bk04` runs, verbatim); `legacy_usd_admission` goes back to `true` only while the
pre-cutover regime is still the live one - after the cutover it stays off (02 §3).

`bk04` proves, on migrations 0001-0009: with both flags off an old-regime job insert and
`resolve_admission_pins` refuse with `55000` and no job, hold or ledger row appears; with
them back on the same insert is accepted; the detectors show no drift.

## Code-only rollback of a migration

D1R's rule, restated so nobody improvises: rolling back **code** is maintenance plus the
previous runtime; the schema stays. Dropping 0006-0009 is D's reverse procedure
(`research/plan/evidence/d/D1R-e538c9a.md`, "Migration / rollback notes"), valid only while
no CREDIT row exists, run by the coordinator after a fresh backup (restore.md A3). Never drop
ledger, journal or job tables to roll back code.

## Verification log

- 2026-09-22 (I3B.c): Maintenance statement drilled verbatim by `bk04` on the pinned image;
  rollout rollback pending I2B's scripts (`rc10`). Nothing run on hosted or the box.
