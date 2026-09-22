"""Q3: the outbox drain, the reconciler and the adapter switch.

PostgreSQL is the authority and the index is a cache (`02` §4): dispatch is at least
once, `JobStore.claim`/`claim_preparation` pick the one winner, and "a reconciler repairs
queued jobs absent from the index and removes stale candidates". This module is that
reconciler plus the relay that feeds it. It talks to two collaborators only:

* `store` - D2's dispatch outbox (`0012_dispatch_outbox.sql`, `PgJobStore`):
  `dispatch_pending(limit=, worker_id=, redelivery_s=)` hands out unacknowledged dispatch
  rows whose job still wants them and stamps them claimed (a claimed row comes back after
  `redelivery_s` without an acknowledgment, and a row whose job moved on is acknowledged
  there as superseded); `acknowledge_dispatch(event_ids)` records delivery;
  `dispatch_snapshot()` is every job that wants a dispatch now, with its latest dispatch
  event. Until D2 merges, the tests drive `tests/q/outboxfake.py`, which is those three
  SQL functions row for row over the contract `FakeJobStore`.
* `index` - a `ports.Scheduler` (`MemoryScheduler` or `ValkeyScheduler`) plus its
  `members()` read.

Nothing here decides who executes. Every rule below is about the index never *missing* a
job PostgreSQL wants dispatched; a duplicate candidate is harmless because the store
fences the claim, so every race is resolved towards "index it again".
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..contracts import errors


@dataclass
class Reconciler:
    """Feeds `index` from `store`'s dispatch outbox and repairs it from PostgreSQL."""

    store: Any
    index: Any
    now: Callable[[], datetime]
    worker_id: str = "q3-relay"
    batch: int = 100
    # Bounded scan: one `drain` reads at most `batch * max_batches` rows. The checkpoint
    # is the store's `claimed_at` stamp - a row handed out is skipped by the next read
    # until `redelivery_s` passes - so consecutive calls walk forward, never rescan.
    max_batches: int = 10
    redelivery_s: float = 30.0
    metrics: dict[str, float] = field(default_factory=lambda: {
        "outbox_lag_s": 0.0})

    # --- (1) the drain -------------------------------------------------------
    async def drain(self) -> dict[str, int]:
        """Deliver pending dispatch rows into the index, then acknowledge them.

        Index first, acknowledgment second: a relay that dies in between leaves the rows
        unacknowledged, the store hands them out again, and `enqueue` is replay safe on
        the event id (a lost acknowledgment costs one redelivery, never a candidate). A
        full index (`capacity_exhausted`) leaves its row unacknowledged, so the retry is
        the redelivery. Any other index failure stops the batch; what was indexed before
        it is still acknowledged, the rest is redelivered.
        """
        report: Counter[str] = Counter()
        for _ in range(self.max_batches):
            events = await self.store.dispatch_pending(limit=self.batch,
                                                       worker_id=self.worker_id,
                                                       redelivery_s=self.redelivery_s)
            report["read"] += len(events)
            if events:
                # How far behind the relay is: the oldest dispatch it just found waiting.
                self.metrics["outbox_lag_s"] = max(
                    (self.now() - event.available_at).total_seconds() for event in events)
            indexed: list[str] = []
            try:
                for event in events:
                    try:
                        report["indexed"] += await self.index.enqueue(event)
                    except errors.CapacityExhausted:
                        report["deferred"] += 1
                        continue
                    indexed.append(event.event_id)
            finally:
                if indexed:
                    report["acknowledged"] += await self.store.acknowledge_dispatch(indexed)
            if len(events) < self.batch:
                break
        return dict(+report)                   # counts that happened, no zeros
