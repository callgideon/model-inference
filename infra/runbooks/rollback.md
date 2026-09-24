# Rollback — a rollout, the maintenance switch

The rules are infra/README.md §8; this is the procedure. Conventions: [README.md](README.md).
Drills: `test_i3b_bk04` (the maintenance switch, real PostgreSQL), `test_i3b_rc10` (this
procedure, steps 2-7: maintenance on PostgreSQL, W2's drain, I2B's `rollback.sh` itself on a
sandbox root with `systemctl`/`docker`/`curl` stubbed, the reaper, the index rebuild and the
reconciliation), `test_i3b_rc10b` (step 4's exit 4: a `/readyz` that never answers).

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
4. **Deploy the previous compatible runtime** with I2B's script, `BACKUP` being the backup
   directory the rollout's `install.sh` printed on its `rollback:` line
   (`/var/backups/infrx/<UTC>-<sha>`):

   ```bash
   sudo ./apps/infrx-api/deploy/rollback.sh "$BACKUP"
   ```

   It stops the runtime units (the worker drains), restores the env file (the image pin),
   the units and the edge files byte for byte, restarts the restored runtime, waits for the
   gateway's and the worker's `/readyz`, and only then reloads the edge. A backup whose
   runtime cannot meter is refused on a pilot host (exit 2, nothing stopped or written):
   stay in [maintenance](#maintenance) instead. Exit 4 is a restored runtime that is not
   ready; the edge is not reloaded (its files on disk are already the backup's). Schema
   changes are never rolled back to roll back code: 0003-0009 are additive and the older
   runtime runs on them (`bk02`, restore.md A7).
5. **Rebuild the index** from PostgreSQL ([index-loss.md](index-loss.md#index-loss)).
6. **Resume admission** only after `/readyz` is ready: [maintenance](#maintenance), exit.
7. **Reconcile** ([reconcile.md](reconcile.md#drift)) and append to the record: durable state
   before and after, and whether any accepted job changed state.

Box-level rollback (the whole root volume) is [restore.md](restore.md#box-snapshot); it also
discards everything written to the root volume since the snapshot, so it comes after this,
not instead of it.

Window: ⚠️ TO BE VERIFIED (P-18) - first measured by the coordinator's I2B rollout drill.

## Known-good rollback drill

I8 (RV-10). What passes: after the rollback **and** after the roll-forward, a fresh public
in-cap video job is accepted, succeeds, its result is fetched and it settles with no wallet
drift. What does not pass: a unit that is active, a `/readyz` that answers, a backup
directory that exists. The previous drill's "5 s to ready" was a runtime restart behind a
running engine (readiness only), onto a backup that held 27af05a - a release with no
preparation loop (F7). Every step below is one coordinator op; log each before it runs
(README rule 1). **Maintenance window, single GPU: no inference while the swap runs.**

1. **Choose the target by its tree and its record**, never by a backup (coordinator host):
   `apps/infrx-api/.venv/bin/python infra/rollout/known-good.py --list --applied 0018 --set S3_MEDIA_BUCKET --set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE --bundles s3://llm-bootcamp-641134885443/releases/`
   (`--applied` = the version hosted's `migrate.py plan` reports). Pick a `KNOWN-GOOD` one.
2. **The box can reinstall it**: `infra/rollout/ssm.sh infra/rollout/steps/85-known-good-box.sh TARGET=<sha>`
   (bundle on the NVMe matches its sha256, commit in the checkout, image cached or not; the
   backups listed with what they hold). Exit 1: run the target's W1 fetch step first.
3. **Close admission**: `infra/rollout/ssm.sh infra/rollout/steps/30-pause.sh RELEASE=<target>`
   (the target's edge files, maintenance, drain), then from the host
   `EXPECT=maintenance infra/rollout/verify-journey.sh` - a submission answers 503 +
   Retry-After: nothing is admitted while the runtime is swapped.
4. **Roll back**: `infra/rollout/ssm.sh infra/rollout/steps/40-checkout.sh RELEASE=<target>`,
   then `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh RELEASE=<target> MIGRATION_DIGEST=nothing-pending ENGINE_MAX_NUM_SEQS=8 INFRX_SET="<the current install's INFRX_SET>"`
   - the engine keeps running (no ENGINE=restart): the install's `timing runtime_ready_s` is
   readiness only; the edge opens after it.
5. **Prove it serves** (host, right after step 4 returns): `infra/rollout/verify-journey.sh`
   with `INFRX_TEST_KEY` (read -rs) and `VIDEO_FILE` (an in-cap clip): the edge opens within
   `EDGE_LAG_MAX_S` (the 503-after-readiness check), the job succeeds, the result has content,
   the usage is authoritative. Then the printed
   `apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --request-id <id>`: `SETTLED`.
6. **Roll forward**: steps 3-5 again with `RELEASE=<the release you rolled back from>`
   (its `50-install.sh` prints `timing engine_s` labelled NOT a cold start and
   `timing runtime_ready_s`).
7. **Record**: the two journeys' output, both `drift.py` verdicts, the SSM command ids and
   timestamps, `85-known-good-box.sh TARGET=<forward sha>` (the new backup and what it
   holds), then append the forward release to `infra/rollout/known-good.json` with its
   evidence (a new entry; never rewrite one).

Any red in 5: stay in maintenance (`95-maintenance.sh RELEASE=<target>`), roll forward
(step 6); if the forward release is the one that failed, the known-good target is the
fallback and the endpoint stays in maintenance until one of them passes step 5. Cold start
is measured separately - `81-restore-artifacts.sh MODE=swap` (engine restart onto restored
weights) and [restart.md](restart.md#engine) - and never quoted from these steps.

## Maintenance

Admission refused, everything else untouched: accepted jobs finish, settle and replay. On
PostgreSQL it is two feature flags (0006); every admission write then fails with SQLSTATE
`55000` and writes nothing, which the ingress maps to a maintenance `503` (D2's mapping).
It exists only once 0006 is applied - today's hosted project (0001-0002) has no flags, so
until the apply the only way to stop admission is to stop the gateway.

**[irreversible: none - but it is a hosted write]** Log it. As `service_role` (the flags are
the one table that role may update), through the pooler:

```sql
-- the pooler login is `postgres` (BYPASSRLS): become the role bk04 drills first
set role service_role;
-- enter maintenance
update infrx.feature_flags set enabled = false, updated_by = 'i3b-drill', reason = 'maintenance drill' where name in ('legacy_usd_admission', 'credit_admission');
-- check: raises SQLSTATE 55000 while in maintenance
select infrx.require_feature('credit_admission');
-- leave maintenance: the same statement with `enabled = true`
reset role;
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
- 2026-09-23 (I3B fix round, RS-6): the SQL block runs under `set role service_role` like
  bk04's `set local role`, instead of as the pooler's `postgres` login (which succeeded by
  BYPASSRLS, not by 0006's policy); rb03 pins it.
- 2026-09-23 (I3B follow-up, rc10): step 4 names I2B's `rollback.sh` and its argument (`rb08`
  pins them to the script's own usage line); `rc10` drills steps 2-7 with the script itself on
  a sandbox root (systemctl/docker/curl stubbed). Nothing run on the box.
- 2026-09-23 (I3B follow-up round 2): `rc10` marks every restored file with its release, so
  the edge files' "byte for byte" is checked (DR-1); `rc10b` drills exit 4 with the gateway's
  or the worker's `/readyz` never answering: no edge reload (DR-3). Nothing run on the box.
- 2026-09-24 (I8): "Known-good rollback drill" added: the target from known-good.py (tree +
  record + bundle), the box check (85), maintenance proven closed, a real job + result +
  settlement after the rollback and after the roll-forward (verify-journey.sh, drift.py
  --request-id), readiness and cold-start timings kept apart. Not run on the box.
