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
2. **Pause admission**: [maintenance](#maintenance). Accepted jobs keep running. After W7f
   (hosted admits CREDIT only), when the target of step 4 runs `legacy_usd` (its backup's env
   file has no `ACCOUNTING_REGIME=credit`), the W7f reversal runs here, from the coordinator
   host, while this runtime's worker still runs: `credit-transition --to legacy_usd`
   ([rollout.md §3](rollout.md#3-rollback-triggers): its keys, why its drain needs that
   worker, the way out if it was stopped first; the database half is drilled by the reversal
   drill below). Step 3 stops the worker, and a CREDIT job it releases then stays in flight.
   A runtime whose worker cannot finish its CREDIT jobs cannot be reversed: stay in maintenance.
3. **Drain and fence**: [restart.md](restart.md#drain). What cannot finish inside the drain
   bound is released to the store; the reaper requeues prepublication attempts after the
   lease TTL. Nothing is settled by the rollback itself.
4. **Deploy the previous compatible runtime** with I2B's script, `BACKUP` being the backup
   directory the rollout's `install.sh` printed on its `rollback:` line
   (`/var/backups/infrx/<UTC>-<sha>`). After W7f, a backup that runs `legacy_usd` only once
   step 2's `credit-transition --to legacy_usd` has exited 0:

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
6. **Resume admission** only after `/readyz` is ready: [maintenance](#maintenance), exit. After a
   W7f reversal (step 2) leave maintenance for `legacy_usd_admission` only — the reversal already
   enabled it — and keep `credit_admission` false: never run the `enabled = true` statement over
   `credit_admission` after a reversal; CREDIT comes back only through a new-key
   `credit-transition --card …` (RB3-RS-1).
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
   `apps/infrx-api/.venv/bin/python infra/rollout/known-good.py --list --applied 0018 --set S3_MEDIA_BUCKET --set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE --set ENGINE_MAX_NUM_SEQS --set ACCOUNTING_REGIME --set ACTIVE_RATE_CARD_VERSION --bundles s3://llm-bootcamp-641134885443/releases/`
   (`--applied` = the version hosted's `migrate.py plan` reports). Pick a `KNOWN-GOOD` one.
   Hosted ahead of the target's tree (e.g. after D10's migrations) is `NOT-KNOWN-GOOD` until
   the target's record carries a `schema_proof` reaching `--applied`: the target's own store
   tests run on the newer schema, recorded as evidence (additive compatibility proven with
   both versions, not assumed).
2. **The box can reinstall it**: `infra/rollout/ssm.sh infra/rollout/steps/85-known-good-box.sh TARGET=<sha>`
   (bundle on the NVMe matches its sha256, commit in the checkout, image cached or not; the
   backups listed with what they hold). Exit 1: run the target's W1 fetch step first.
3. **Reverse W7f first**, while the current release still runs, when hosted admits CREDIT only
   and the target runs `legacy_usd` (both known-good targets do): from the coordinator host
   with `OPERATIONS_DATABASE_URL` (owner, `read -rs`) and `INFRX_OPERATOR_KEY` exported,
   `python -m infrx.operations.cli credit-transition --to legacy_usd --drain-timeout-s 900
   --idempotency-key revert-<drill id> --reason "<drill id> rollback to legacy_usd"`
   (`--dry-run` first: it writes nothing). From the freeze on, a CREDIT submission answers 503
   (the release admits CREDIT only). Its drain needs the current release's worker, which
   finishes the CREDIT jobs already accepted (they settle in CREDIT); 3b's pause stops it, and
   a job it releases then stays in flight. Exit 1 (`in_flight`/`open_transactions`) leaves
   CREDIT frozen and nothing audited: rerun under the same key while the worker runs. After a
   pause (exit 1 `in_flight` with the worker stopped): on the box `systemctl start
   infrx-worker` (the edge stays in maintenance), the same-key rerun until it exits 0, then
   `systemctl stop infrx-worker`. Proof (the database half; the box half below is an operator
   step no test runs): `apps/infrx-api/tests/g/ops/test_reversal_pg.py::test_reversal_pg__credit_back_to_legacy_usd_drains_keeps_credit_exact_and_replays_nothing`
   ([rollout.md §3](rollout.md#3-rollback-triggers)).
3b. **Close admission**: `infra/rollout/ssm.sh infra/rollout/steps/30-pause.sh RELEASE=<target>`
   (the target's edge files, maintenance, drain), then from the host
   `EXPECT=maintenance infra/rollout/verify-journey.sh` - a submission answers 503 +
   Retry-After: nothing is admitted while the runtime is swapped.
4. **Roll back**: `infra/rollout/ssm.sh infra/rollout/steps/40-checkout.sh RELEASE=<target>`,
   then `TIMEOUT_S=3600 infra/rollout/ssm.sh infra/rollout/steps/50-install.sh RELEASE=<target> MIGRATION_DIGEST=nothing-pending ENGINE_MAX_NUM_SEQS=8 INFRX_SET="<the current install's INFRX_SET>"`
   (after step 3, without `ACCOUNTING_REGIME` and `ACTIVE_RATE_CARD_VERSION`: the target runs
   `legacy_usd`)
   - the engine keeps running (no ENGINE=restart): the install's `timing runtime_ready_s` is
   readiness only; the edge opens after it. Then the reinstall rule
   ([rollout.md §3](rollout.md#3-rollback-triggers)): `infra/rollout/ssm.sh
   infra/rollout/steps/55-runtime-login.sh` only when the target carries R127's dedicated
   logins; 4226315 and bda1586 do not, so never after them (they stay on `pg_journal_url`).
5. **Prove it serves** (host, right after step 4 returns): `infra/rollout/verify-journey.sh`
   with `INFRX_TEST_KEY` (read -rs) and `VIDEO_FILE` (an in-cap clip): the edge opens within
   `EDGE_LAG_MAX_S` (the 503-after-readiness check), the job succeeds, the result has content,
   the usage is authoritative. Then the printed
   `apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --request-id <id>`: `SETTLED`.
6. **Roll forward**: first (the forward release runs `credit`) W7f's
   `credit-transition --card …` with the card and rates restated, under a **new** key (the
   W7f key's replay writes nothing, so CREDIT stays off: the proof in step 3), while the
   target still runs: its drain needs the target's worker for the USD jobs already accepted,
   as step 3's does for CREDIT (the same way out). Then step 3b, then step 4 and 5
   with `RELEASE=<the release you rolled back from>` and its own `INFRX_SET` (its
   `50-install.sh` prints `timing engine_s` labelled NOT a cold start and
   `timing runtime_ready_s`), with `55-runtime-login.sh` right after its 50-install (it carries
   R127: the reinstall rule).
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
`signup_grant` (the App's cutover rollback) is never written here but with the audited
operator verb
`python -m infrx.operations.cli flag --name signup_grant --off --idempotency-key <k> --reason "<why>"`
(`infrx.set_feature_flag`, audited once per key, R144), which refuses the two admission flags
(they move with `credit-transition`, R133).

Decided 2026-09-26 (WR-G8FLAG-3, R144 amendment): this direct statement is the recorded exception to R144 — under maintenance no metered runtime exists, and the drill sets both admission flags false as `service_role`, pinned word for word by rb03/bk04; it is a recovery action, not an operator regime change. Every other regime-flag write goes through `credit-transition` (R133) or the audited `flag` verb (G8-FLAG).

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
- 2026-09-26 (G8-FLAG, GAP-I3-1/I3R-7): Maintenance names the audited `flag` verb for
  `signup_grant` (R144); the maintenance statement itself is unchanged (bk04/rb03). Local only.
- 2026-09-26 (G8-FLAG fix round, review 1-G8FLAG-R2): Maintenance marks the direct regime-flag
  statement's conflict with R144 as TO BE VERIFIED (WR-G8FLAG-3); the statement is unchanged.
  Local only.
- 2026-09-26 (E4C-RUNBOOK-2 wiring): the known-good drill's `--list` line passes every install name rollout.md §3 sets (`ENGINE_MAX_NUM_SEQS`, `ACCOUNTING_REGIME`, `ACTIVE_RATE_CARD_VERSION` added; measured by the lane: 4226315 and bda1586 exit 0 KNOWN-GOOD with the eight names at --applied 0023).
- 2026-09-26 (RUNBOOK-3): the rollout rollback's step 4 and the drill (3b) run the W7f
  reversal (`credit-transition --to legacy_usd`) before putting a `legacy_usd` release back;
  the drill installs the target without the regime names, runs `55-runtime-login.sh` only
  after an R127 release (the roll-forward, never 4226315/bda1586) and re-activates CREDIT
  under a new key. Proof of the database half: `test_reversal_pg.py`; runbook shape:
  `tests/integration/ops/test_runbook_reversal.py`. Not run on the box or hosted.
- 2026-09-26 (RUNBOOK-3 fix round, review 0-RV3-1): the reversal's drain needs the worker of
  the release it reverses, and the pause stops it (a job it releases stays in flight: every
  same-key rerun would exit 1 `in_flight`). The rollout rollback runs the reversal at step 2,
  before step 3's drain; the drill reverses W7f at step 3, before the pause (now 3b); the
  roll-forward's `credit-transition --card` runs before 3b too; the way out after a pause
  (`systemctl start infrx-worker`, the same-key rerun, stop) is named. Runbook shape: rv03;
  the PG drill reruns K2 with no worker (exit 1, nothing written). Not run on the box or hosted.
