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

A full index (`capacity_exhausted`) stops the pump and hands the unindexed rows back
(`release_dispatch`) for the next pump: the index is a cache with a bound, never a place a
job can be dropped. Any other enqueue failure is recorded on its row and the batch goes
on; the first such failure is raised after the rest were acknowledged.
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
        taken, deferred, failures = [], [], []
        for position, event in enumerate(events):
            try:
                await self.scheduler.enqueue(event)     # True new, False already indexed
            except errors.CapacityExhausted:
                # The index is full: stop, and hand this row and the rest back now rather
                # than leaving them stamped claimed for a whole redelivery window (OB-4).
                deferred = [e.event_id for e in events[position:]]
                break
            except Exception as failed:
                # One bad row must not strand the batch behind it (OB-7): record it, go on,
                # and raise once the others are safely indexed and acknowledged.
                failures.append(failed)
                await self.store.record_dispatch_error(event.event_id, repr(failed)[:500])
                continue
            taken.append(event.event_id)
        acknowledged = await self.store.acknowledge_dispatch(taken) if taken else 0
        if deferred:
            await self.store.release_dispatch(deferred)
        if failures:
            raise failures[0]
        return {"read": len(events), "indexed": len(taken), "acknowledged": acknowledged,
                "deferred": len(deferred)}

    async def rebuild(self) -> int:
        since = await self.store.db_now()                 # BEFORE the snapshot (the fence)
        indexed = await self.scheduler.rebuild(await self.store.dispatch_snapshot())
        await self.store.reopen_dispatch(since)
        await self.pump()
        return indexed
