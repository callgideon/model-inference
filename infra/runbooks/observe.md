# Observe — continuous monitoring, the synthetic check, alert delivery (I8)

What runs on the box once `72-observe-install.sh` has run (coordinator op, see
[rollout.md](rollout.md) §4): `infrx-observe.timer` every minute runs
`infra/observe/observe.sh` — the host probe, the durable-truth exporter
(`durable.py`, one read-only transaction through the transaction pooler), the evaluator
(`python -m infrx.observe.alerts`) over the gateway's and the worker's loopback `/metrics`
and the three textfiles in `/var/lib/infrx/metrics/`, then `deliver.py`.
`infrx-canary.timer` every 10 minutes runs `canary.sh` (one tiny text and one in-cap video
request through the public edge as the canary tenant). The rule set is
`infra/alerts/alerts.json` (I3B, version 1) plus `infra/alerts/operations.json` (I8,
version 2), merged by `infra/observe/rules.py`; a delivered message names the merged
version (`a1+o2`). Conventions: [README.md](README.md) (log before you act; names, never
values). Single GPU: every "engine down" alert below is an outage until the engine is
back — there is no second replica (P-16).

```bash
# box, read-only: what the monitor sees now (the step 73-observe-status.sh does exactly this)
systemctl list-timers 'infrx-*' --no-pager; systemctl --failed --no-pager
ls -l --time-style=+%FT%TZ /var/lib/infrx/metrics/
cat /var/lib/infrx/metrics/host.prom /var/lib/infrx/metrics/durable.prom /var/lib/infrx/metrics/canary.prom
journalctl -u infrx-observe -u infrx-canary --since -30min --no-pager | tail -n 60
```

## Exporter down

`DurableExporterDown` / `ScrapeFailed`: the monitor cannot see PostgreSQL (or a source).
Every durable alert is blind until it is back — treat as a page.

1. `journalctl -u infrx-observe --since -15min` — the exporter prints the error class and
   SQLSTATE only. `EMAXCONN…`: the pooler budget ([pool_budget.py](pool_budget.py),
   step `71-pool-budget.sh`); `28P01`: the monitor login (`/etc/infrx-observe.env`,
   D10's read-only role); a timeout: hosted Supabase status.
2. The runtime is unaffected by the exporter; do not restart the runtime for this.

## Durable backlog

`ReadyBacklogOld`: a dispatch PostgreSQL made available has not been claimed within the
async queue budget. Durable truth, not the Valkey index: if `QueueStalled` (index) is quiet
while this fires, the index lost the candidate — [index-loss.md](index-loss.md#index-loss)
(rebuild from PostgreSQL). If both fire, the worker is not claiming:
[restart.md](restart.md#worker).

## Stuck jobs

`PreparingStuck`, `QueuedStuck`, `RunningStuck`, `OverdueJobs`, `PlatformFailuresRecent`.
Check the engine first (`EngineDown`, `GpuUnavailable`), then the worker's journal
(`journalctl -u infrx-worker --since -30min | tail -n 80`). `OverdueJobs` means the reaper
is not terminalizing jobs past their deadline: a worker restart runs `recover()`
([restart.md](restart.md#worker)). Never edit a job row by hand
([README.md](README.md) rule 5); afterwards run [reconcile.md](reconcile.md#drift).

## Stuck holds

`StuckHolds`: a hold is still `held` for a job that is terminal or long past its deadline —
money is reserved that nothing will settle. Page. Collect the counts with
`infra/runbooks/drift.py` (read-only), then [reconcile.md](reconcile.md#unsettleable). No
hand edit of holds or wallets.

## GC lag

`OutboxGcLag`, `StreamChunkGcLag`: retention jobs are not running — the worker's GC
(`gc_outbox`) or the journal expiry. `ResultContentPastExpiry` fires until D10/M6's
content cleanup ships (RV-03/RV-11): result bodies are immutable rows today and nothing
removes them; it is a known, truthful ticket, not noise to silence.

## Units

`UnitInactive{unit=…}`: `systemctl status <unit> --no-pager`; the runtime units refuse to
start on an env file their image does not accept (`preflight.py envcheck` in
ExecStartPre: the journal names the setting). Then [restart.md](restart.md).

## Edge

`EdgeInMaintenance`: the maintenance site is active (a drain, a rollout or a rollback);
expected inside a window, a ticket outside one — `drain.sh resume` only once the runtime
is ready ([rollback.md](rollback.md#maintenance)). `PublicEdgeDown`: public `/health` does
not answer 200 while the box's own readiness may: DNS, TLS, Caddy, or the
503-after-readiness seen on 2026-09-24 — `docker logs --since 10m caddy`,
`curl -s -o /dev/null -w '%{http_code}' https://marlin2b.callbill.ai/health`.
`GatewayBodySlotsFull` (pending WR-I8-3): every large-body slot is in use; refusals are
typed 429s, see [restart.md](restart.md#saturation).

## Canary

`CanaryFailed{kind=text|video}`: the public path does not serve a real request — page even
if every component says healthy. `canary.sh` prints `http=` and seconds per kind in the
journal. `CanaryNotConfigured`: `/etc/infrx-canary.env` lacks `INFRX_CANARY_KEY` (the
canary tenant's scoped key, from SSM by `72-observe-install.sh`). `CanaryStale`: the timer
is not running (`systemctl list-timers`). The canary is bounded (one text + one in-cap
video per 10 minutes, max_tokens 8/16) and charged to the canary tenant (P-24):
`72-observe-install.sh` takes the key's SSM name with no default and enables the timer only
with `P24_APPROVED=<ref>`; without it `CanaryStale`/`CanaryFailed` cannot fire (no samples)
and nothing proves the public path between drills.

## Database pool

`DbPoolWaiting`, `DbPoolTimeouts`, `DbPoolConnectionErrors` (pending WR-I8-2): a runtime pool
waited, gave up or could not connect. Run `71-pool-budget.sh`; a FAIL there is the cause
(the session pooler's 15 slots). Transaction mode for the runtime needs WR-I8-1 first.

## Delivery test

P-25 supplies the destination (`ALERT_WEBHOOK_URL`), the owner (`ALERT_OWNER`) and the
escalation (`ALERT_ESCALATION`) in `/etc/infrx-alert.env` (root 0600). Until then delivery
is **BLOCKED**: `deliver.py` keeps each message in `/var/lib/infrx/metrics/undelivered.jsonl`
(bounded to 200) and exits 3, so `infrx-observe.service` shows failed. The proof, once P-25
is in place (coordinator, box, one step each):

1. `infra/rollout/ssm.sh infra/rollout/steps/74-alert-test.sh` — one clearly marked
   `[TEST FIRING] … NO ACTION REQUIRED` message with a nonce; the step prints `http=2xx`
   and the nonce.
2. The owner confirms receipt of that nonce (screenshot or message link in the session
   record) — a 2xx alone is not delivery.
3. `infra/rollout/ssm.sh infra/rollout/steps/74-alert-test.sh RESOLVE=<nonce>` — the
   recovery message; the owner confirms it too.

## Retention stalled

`RetentionAborting` (3 passes in a row stopped early), `RetentionStale` (no completed pass
for claim TTL + 3 intervals) and `RetentionPendingDeleteOld` (a delete left unfinished
longer than claim TTL + 2 intervals) are M6's `RetentionCollector` in the worker (pending
WR-I8-M6-1: the worker records its `Report`). Expired uploads and prepared media are not
being deleted; nothing is lost, but content outlives its retention. Read the worker's
`retention sweep:` log lines (`journalctl -u infrx-worker --since -1h | grep 'retention sweep'`):
`aborted: dependency_unavailable` is PostgreSQL (see [Exporter down](#exporter-down) and
[Database pool](#database-pool)); `aborted: object_store_unavailable` is the bucket (the
instance role, `S3_MEDIA_BUCKET`, the endpoint). No log lines at all: the worker is not
running the collector (`systemctl status infrx-worker`). Do not delete objects by hand: a
delete is authorized only by the collector's committed tombstone. Thresholds assume a
300 s interval, ⚠️ TO BE VERIFIED (P-25).

## Retention delete failures

`RetentionDeleteFailures`: an object-store delete failed; that pass stopped and the content
stays tombstoned (registration of the same key answers `content_retiring`, retryable) until
a later claim deletes it. One is noise from the store; repeated ones become
`RetentionAborting` - treat as [Retention stalled](#retention-stalled).

## Processing cache high water

`ProcessingCacheRefusing`: a preparation found `PROCESSING_CACHE_DIR` above its high water
(`PROCESSING_CACHE_MAX_BYTES`, ⚠️ TO BE VERIFIED (P-25)) with everything left pinned by
in-flight attempts, and refused with a retryable 503. Check `infrx_processing_cache_bytes`
(host probe) against the setting, and in-flight attempts against the worker's concurrency;
a cache full of pins with nothing running is a pin leak (the worker engine's, M6 wiring 2).
`ProcessingCacheLarge` is the same volume by bytes ([disk.md](disk.md#disk-filling)).

## Bucket lifecycle rule

Defence in depth for M6 (wiring 4): `apps/infrx-api/deploy/s3-lifecycle.json` aborts
multipart uploads under the media prefix (`S3_MEDIA_PREFIX`, default `infrx/`) left
incomplete for a day, so an upload the collector never saw leaves no parts. It never
expires a completed object: deletion is the collector's. Coordinator op, once per bucket,
names only (the bucket is the pinned `S3_MEDIA_BUCKET`). The bucket is shared, and a put
**replaces** its whole lifecycle configuration, so read the existing rules first and add
this one to them:

```bash
aws s3api get-bucket-lifecycle-configuration --bucket "$S3_MEDIA_BUCKET" > lifecycle-before.json \
  || echo '{"Rules": []}' > lifecycle-before.json    # NoSuchLifecycleConfiguration: none yet
jq -s '{Rules: ([.[0].Rules[] | select(.ID != "infrx-abort-incomplete-multipart")] + .[1].Rules)}' \
  lifecycle-before.json apps/infrx-api/deploy/s3-lifecycle.json > lifecycle-after.json
aws s3api put-bucket-lifecycle-configuration --bucket "$S3_MEDIA_BUCKET" \
  --lifecycle-configuration file://lifecycle-after.json
aws s3api get-bucket-lifecycle-configuration --bucket "$S3_MEDIA_BUCKET"   # the rule is listed
```

Keep `lifecycle-before.json` with the session record (it is the rollback). Not applied yet.

## Verification log

- 2026-09-24 (I8): written with the units, scripts and rules it describes; tests in
  `apps/infrx-api/tests/i/test_observe.py`. Nothing here has run on the box; delivery is
  BLOCKED on P-25.
- 2026-09-25 (I8, M6 wiring 4): retention and processing-cache sections, the bucket
  lifecycle rule and its apply stanza; rule set version `a1+o2`. The families are pending
  WR-I8-M6-1 (no producer at M6 phase 2 `8fe53ed9`); nothing applied to the bucket.
