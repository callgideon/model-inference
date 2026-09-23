"""D2: the outbox relay - PostgreSQL dispatch rows into a `ports.Scheduler`.

At-least-once, and harmless when it repeats: `Scheduler.enqueue` is replay-safe on the
stable event id, and the index never authorizes execution (`claim_preparation`/`claim`
do). So the relay's only duty is that no accepted job is ever missing from the index:

* `pump`: read pending rows, enqueue each, THEN acknowledge the ones the index took. A
  relay that dies between the two leaves the rows pending; they are handed out again after
  `redelivery_s` (lost acknowledgment -> redelivery -> the same candidate, once).
* `rebuild`: after the index lost its data (a Valkey restart), rebuild it from
  `dispatch_snapshot` - PostgreSQL truth, never the index's memory. Q2: a rebuild clears
  the index's acknowledged set, so acknowledge claimed candidates before rebuilding.
  A rebuild REPLACES the index, so when a rebuild and a pump run in two processes a job
  admitted after the snapshot began, then pumped and acknowledged by the other relay
  before the rebuild landed, would be wiped from the index with its row marked delivered.
  The fence: read the store clock BEFORE the snapshot, rebuild, `reopen_dispatch(since)`
  (every row acknowledged/claimed since then that still names wanted work is pending
  again), then pump. Two relays in two processes require this reopen; `enqueue` is
  replay-safe on the stable event id, so re-sending costs nothing.

A full index (`capacity_exhausted`) leaves the row pending for the next pump: the index
is a cache with a bound, never a place a job can be dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts import errors
from .jobstore import PgJobStore


@dataclass
class OutboxRelay:
    store: PgJobStore
    scheduler: object                      # `ports.Scheduler`
    worker_id: str = "relay"
    batch: int = 100
    redelivery_s: float = 30.0

    async def pump(self) -> dict[str, int]:
        events = await self.store.dispatch_pending(limit=self.batch, worker_id=self.worker_id,
                                                   redelivery_s=self.redelivery_s)
        taken, deferred = [], 0
        for event in events:
            try:
                await self.scheduler.enqueue(event)     # True new, False already indexed
            except errors.CapacityExhausted:
                deferred += 1                            # stays pending; retried later
                continue
            taken.append(event.event_id)
        acknowledged = await self.store.acknowledge_dispatch(taken) if taken else 0
        return {"read": len(events), "indexed": len(taken), "acknowledged": acknowledged,
                "deferred": deferred}

    async def rebuild(self) -> int:
        since = await self.store.db_now()                 # BEFORE the snapshot (the fence)
        indexed = await self.scheduler.rebuild(await self.store.dispatch_snapshot())
        await self.store.reopen_dispatch(since)
        await self.pump()
        return indexed
