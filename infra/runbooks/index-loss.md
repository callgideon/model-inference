# Index loss — the scheduling index is a cache

Conventions: [README.md](README.md). Valkey holds the scheduling index and nothing durable:
it runs with no RDB and no AOF (Q2), so a restart **is** a total loss, and that is fine -
every accepted job is in PostgreSQL and the index is rebuilt from it. Drills:
`test_i3b_rc06` (Valkey SIGKILLed while workers run: the next claim fails loudly rather than
looking idle, Valkey returns empty, the index is rebuilt from the durable snapshot and every
remaining job runs exactly once), `test_i3b_rc09` (the same inside a whole-host loss), E3B's
`dr13` (loss and rebuild, index half).

## Index loss

Signals: `ComponentDown{component="index"}`, worker logs with Valkey connection errors, the
queue depth dropping to 0 while accepted jobs are `queued` in PostgreSQL, `QueueStalled`.

1. Bring Valkey back (**PENDING I2B**: container/unit name; proposed `infrx-valkey`):
   ```bash
   # step: valkey-restart.sh (SSM)
   set -euo pipefail
   docker restart infrx-valkey || systemctl restart infrx-valkey.service
   for i in $(seq 1 30); do valkey-cli -p 6379 ping 2>/dev/null | grep -q PONG && break; sleep 1; done
   ```
2. Rebuild from PostgreSQL. The reconciler reads every accepted, non-terminal job that
   should be dispatchable and calls `Scheduler.rebuild(snapshot)` (Q2's atomic script:
   it clears in-flight, acknowledged and fairness state and indexes the snapshot).
   **PENDING Q3** - its command goes here. Until Q3 lands there is no production rebuild,
   and a restarted Valkey stays empty until jobs are re-dispatched by the reaper.
3. Let the reaper run once (leases that were in flight at the loss expire after
   `LEASE_TTL_S` = 120 s and are requeued; the preparation ones after 30 s).
4. Verify: index depth equals the number of queued jobs in PostgreSQL; every job ends
   terminal exactly once ([reconcile.md](reconcile.md#drift)). Duplicate candidates are
   harmless - the store's claim decides and the loser is acknowledged (r1 R46).

Window: ⚠️ TO BE VERIFIED (P-18) - Valkey restart plus rebuild plus one reaper period.
`meas. local` (`rc06`): the whole drill including the container restart takes seconds on
E2's stack.

## Queue stalled

`QueueStalled`: the oldest available candidate has waited longer than an async job's queue
budget (600 s). The index is alive but nothing is consuming it. In order: are workers
running and claiming ([restart.md](restart.md#worker))? Is the engine up
([restart.md](restart.md#engine))? Is the reaper running (leases expiring but nothing
requeued)? Interactive jobs will already have expired `queue_wait_expired` (free); async
jobs keep their place.

## Queue saturated

`QueueSaturated` (> 80 % of `MAX_INDEX_ITEMS`): admission will soon refuse with
`capacity_exhausted` (a 429 with `Retry-After`). This is load, not failure - see
[restart.md](restart.md#saturation). Raising the cap is a Q/W4 decision from measurement.

## Verification log

- 2026-09-22 (I3B.c): Written from `rc06`/`rc09` on E2's Valkey through Q2's adapter. The
  production rebuild is PENDING Q3; Valkey's unit name PENDING I2B. Nothing run on the box.
