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
        return await self.scheduler.rebuild(await self.store.dispatch_snapshot())
