"""Q3: the outbox drain, the reconciler and the adapter switch.

PostgreSQL is the authority and the index is a cache (`02` §4): dispatch is at least
once, `JobStore.claim`/`claim_preparation` pick the one winner, and "a reconciler repairs
queued jobs absent from the index and removes stale candidates". This module is that
reconciler plus the relay that feeds it. It talks to two collaborators only:

* `store` - D2's dispatch outbox (`0012_dispatch_outbox.sql`, `PgJobStore`):
  `dispatch_pending(limit=, worker_id=, redelivery_s=)` hands out unacknowledged dispatch
  rows whose job still wants them and stamps them claimed by `worker_id` (a claimed row
  comes back after `redelivery_s` without an acknowledgment, and a row whose job moved on
  is acknowledged there as superseded); `acknowledge_dispatch(event_ids, worker_id=)`
  records delivery, and lands only while this relay still holds the claim (D2 OB-1b);
  `dispatch_snapshot()` is every job that wants a dispatch now, with its latest dispatch
  event. Until D2 merges, the tests drive `tests/q/outboxfake.py`, D2's dispatch
  functions row for row over the contract `FakeJobStore`.
* `index` - a `ports.Scheduler` (`MemoryScheduler` or `ValkeyScheduler`) plus its
  `members()` read.

Nothing here decides who executes. Every rule below is about the index never *missing* a
job PostgreSQL wants dispatched; a duplicate candidate is harmless because the store
fences the claim, so every race is resolved towards "index it again".
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..contracts import errors

log = logging.getLogger(__name__)


@dataclass
class Reconciler:
    """Feeds `index` from `store`'s dispatch outbox and repairs it from PostgreSQL."""

    store: Any
    index: Any
    now: Callable[[], datetime]
    # Unique per relay (D2 OB-1b): the store lets only the claiming worker acknowledge,
    # which separates two relay processes only if their ids differ.
    worker_id: str = field(default_factory=lambda: f"q3-relay-{uuid.uuid4().hex[:8]}")
    batch: int = 100
    # Bounded scan: one `drain` reads at most `batch * max_batches` rows. The checkpoint
    # is the store's `claimed_at` stamp - a row handed out is skipped by the next read
    # until `redelivery_s` passes - so consecutive calls walk forward, never rescan.
    max_batches: int = 10
    redelivery_s: float = 30.0
    # Gauges from the last drain/pass, plus the rebuild count. `outbox_lag_s`: age of the
    # oldest dispatch row the last drain found waiting (0 when none waited).
    # `missing_index` / `missing_lag_s`: jobs PostgreSQL wants dispatched that the index
    # did not hold, and the oldest one's age.
    # `dead_candidates`: candidates removed because their job no longer wants dispatch.
    metrics: dict[str, float] = field(default_factory=lambda: {
        "outbox_lag_s": 0.0, "missing_index": 0, "missing_lag_s": 0.0,
        "dead_candidates": 0, "rebuilds": 0, "errors": 0})

    # --- the loop ------------------------------------------------------------
    async def run(self, stop: asyncio.Event, *, drain_every_s: float = 0.05,
                  reconcile_every_s: float = 10.0) -> None:
        """Drain every tick and reconcile every `reconcile_every_s` until `stop` is set.

        A failure of either - Valkey restarting, PostgreSQL unreachable, a row the index
        rejects - is logged, counted in `metrics["errors"]` and retried at the next tick (a
        failed pass stays due): the relay has to outlive the outages it exists to repair.
        The two run as two concurrent loops, so a pass that keeps failing, however slowly
        (the unbounded snapshot timing out under a backlog), never holds the drain back
        (reviews DUR-1, DUR-1b). The first tick reconciles, so a process starting against
        an index that lost its data repairs it at once.
        """
        async def every(step, period_s: float, retry_s: float) -> None:
            while not stop.is_set():
                try:
                    await step()
                    wait = period_s
                except Exception:
                    self._failed(step.__name__)
                    wait = retry_s                     # a failed step stays due
                try:
                    await asyncio.wait_for(stop.wait(), wait)
                except TimeoutError:
                    pass

        await asyncio.gather(every(self.reconcile, reconcile_every_s, drain_every_s),
                             every(self.drain, drain_every_s, drain_every_s))

    def _failed(self, step: str) -> None:
        self.metrics["errors"] += 1
        log.warning("scheduling relay: %s failed, retrying", step, exc_info=True)

    # --- (1) the drain -------------------------------------------------------
    async def drain(self) -> dict[str, int]:
        """Deliver pending dispatch rows into the index, then acknowledge them.

        Index first, acknowledgment second: a relay that dies in between leaves the rows
        unacknowledged, the store hands them out again, and `enqueue` is replay safe on
        the event id (a lost acknowledgment costs one redelivery, never a candidate). A
        full index (`capacity_exhausted`) stops the drain and hands the refused row and
        the rest back (`release_dispatch`, D2 OB-4), so the next tick retries them, not a
        whole `redelivery_s` later. Any other index failure stops the batch too: what was
        indexed before it is still acknowledged, the rows after it are handed back, and
        the failing row keeps its claim - a row the index keeps rejecting waits for its
        redelivery instead of blocking the rows behind it at every tick (review DUR-6).
        """
        report: Counter[str] = Counter()
        self.metrics["outbox_lag_s"] = 0.0          # nothing waiting is no lag (DUR-3)
        for _ in range(self.max_batches):
            events = await self.store.dispatch_pending(limit=self.batch,
                                                       worker_id=self.worker_id,
                                                       redelivery_s=self.redelivery_s)
            report["read"] += len(events)
            if events:
                # How far behind the relay is: the oldest dispatch it just found waiting.
                self.metrics["outbox_lag_s"] = max(
                    self.metrics["outbox_lag_s"],
                    *((self.now() - event.available_at).total_seconds() for event in events))
            indexed: list[str] = []
            rest: tuple = ()                   # read, not indexed: handed back at once
            try:
                for position, event in enumerate(events):
                    rest = events[position + 1:]           # this one fails: it keeps its claim
                    try:
                        report["indexed"] += await self.index.enqueue(event)
                    except errors.CapacityExhausted:
                        rest = events[position:]           # full: this row goes back too
                        break
                    indexed.append(event.event_id)
            finally:
                # Hand back first: an acknowledgment that fails must not keep the rest
                # claimed for a whole redelivery window (review DUR-9).
                if rest:
                    await self.store.release_dispatch([event.event_id for event in rest])
                if indexed:
                    report["acknowledged"] += await self.store.acknowledge_dispatch(
                        indexed, worker_id=self.worker_id)
            report["deferred"] += len(rest)
            if rest or len(events) < self.batch:
                break
        return dict(+report)                   # counts that happened, no zeros

    # --- (2) the reconciler --------------------------------------------------
    async def reconcile(self) -> dict[str, int]:
        """One pass of the index against PostgreSQL's dispatch snapshot (`02` §4).

        * **Dead** candidates - their job is running, terminal, cancelled or preparing
          under a live lease - are removed. The index is read *before* the snapshot, so a
          candidate delivered after the snapshot is never judged by it.
        * **Missing** jobs - wanted by PostgreSQL, absent from the index - are enqueued
          (replay safe). The index refuses one it has already *acknowledged*: it handed
          the candidate to a worker whose claim did not take, the outbox row is long
          acknowledged, and nothing short of a rebuild clears that - so the pass rebuilds.

        Runs concurrently with the drain and the workers. Its races resolve towards a
        duplicate candidate, which the store's claim fences, or towards a candidate
        missing until the next pass - never towards a lost job.
        """
        members = await self.index.members()
        snapshot = await self.store.dispatch_snapshot()
        wanted = {event.job_id for event in snapshot}
        dead = sorted({job_id for job_id in members.values() if job_id not in wanted})
        for job_id in dead:
            await self.index.remove(job_id)
        if dead:
            # A job re-dispatched between that snapshot and the removal lost its new
            # candidate with the dead one; only a later snapshot shows it.
            snapshot = await self.store.dispatch_snapshot()
        report, oldest = await self._top_up(snapshot, self.index)
        self.metrics.update(missing_index=report["missing"], missing_lag_s=oldest,
                            dead_candidates=len(dead))
        report["dead"] = len(dead)
        if report.pop("blocked", 0):
            report["rebuilt"] = await self.rebuild()
        return dict(+report)

    async def rebuild(self, index: Any = None) -> int:
        """Replace `index` (default: the live one) with PostgreSQL's truth; returns the
        number of candidates it then holds from the snapshots.

        Q2's hole, closed here: a rebuild wipes whatever was delivered after its snapshot
        was read, and that delivery's outbox row may already be acknowledged, so it will
        never come back. A **second** snapshot, read after the rebuild, sees every job
        that still wants dispatch, and topping up from it re-indexes exactly those
        (`enqueue` is replay safe, and the rebuild cleared the acknowledged set). The
        caps are not applied to the rebuild itself: it is recovery.

        A worker that acknowledges a re-indexed candidate between the rebuild and the
        top-up (its claim never landed) leaves the top-up `blocked`; the rebuild then runs
        once more rather than leaving the job unindexed until the next pass (DUR-4). At
        most twice: if the second is blocked too, the count leaves the blocked candidates
        out and the next pass rebuilds again (DUR-4b).
        """
        index = self.index if index is None else index
        for _ in range(2):
            count = await index.rebuild(await self.store.dispatch_snapshot())
            self.metrics["rebuilds"] += 1              # the index was replaced (DUR-4b)
            report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)
            if not report["blocked"]:
                break
        return count + report["repaired"] - report["blocked"]

    # --- (3) switching adapters ---------------------------------------------
    async def switch(self, index: Any) -> int:
        """Move dispatch to another adapter (memory <-> Valkey) without losing a job.

        The new index is rebuilt from PostgreSQL before anything points at it; then the
        drain points at it; then it is topped up from a snapshot read after the swap,
        which carries over a dispatch the drain delivered into the old index meanwhile
        (its outbox row is acknowledged, so nothing else would). The old index is never
        copied: every job it holds that still wants dispatch is in the snapshot. A worker
        still claiming from it can at worst be offered a job the new index also offers,
        which the store's claim fences; the composition root re-points the workers and
        drops the old index.
        """
        count = await self.rebuild(index)
        self.index = index
        report, _ = await self._top_up(await self.store.dispatch_snapshot(), index)
        return count + report["repaired"]

    async def _top_up(self, snapshot, index) -> tuple[Counter[str], float]:
        """Enqueue every snapshot event the index does not hold. `blocked` counts the
        ones it refused as already acknowledged and still does not hold."""
        present = await index.members()
        missing = [event for event in snapshot if event.event_id not in present]
        report: Counter[str] = Counter(missing=len(missing))
        refused = []
        for event in missing:
            try:
                if await index.enqueue(event):
                    report["repaired"] += 1
                else:
                    refused.append(event.event_id)
            except errors.CapacityExhausted:
                report["deferred"] += 1           # the next pass retries it
        if refused:
            present = await index.members()
            report["blocked"] = sum(event_id not in present for event_id in refused)
        oldest = max(((self.now() - event.available_at).total_seconds()
                      for event in missing), default=0.0)
        return report, oldest
