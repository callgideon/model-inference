"""M6: durable content retention - the collector deletes only what the lifecycle store
says may go (RV-03).

`gc.MediaCollector` decided liveness from its own process maps: a restarted gateway saw
no live job and deleted a running job's source once the grace had passed
(`research/plan/evidence/v1-review-20260924/reproduce.py`). This collector keeps nothing
between passes. The authority is F2C's `ContentLifecycle` port (D10.b on PostgreSQL): one
durable row per content object and generation, persisted eligibility, and leased, fenced
claims. One pass:

1. **candidates**, `page_size` at a time, from the store: live rows past their persisted
   `eligible_at` with no live reference and no unexpired claim, plus tombstones whose
   delete never finished. A bucket listing or an empty process map is never evidence.
2. per candidate, **claim** (a lease on ONE generation, with the next fence), then
   **tombstone**: the store rechecks references and grace inside that transaction, under
   the row lock admission/attach also take. Any refusal keeps the object. A collector whose
   claim expired gets `claim_lost` and never reaches step 3.
3. only after the tombstone commits, the **external delete**, outside every lock and
   idempotent. Objects are addressed by generation (`generation_key`), so a delete that is
   delayed past its lease can only remove the generation it tombstoned - never the next
   one written later under the same logical key. Database content is scrubbed through the
   store (`lifecycle.scrub`), which keeps the row's metadata (D3).
4. **acknowledge_delete**. A lost acknowledgement leaves the row tombstoned: unreadable,
   and a candidate again once the claim lapses, when the delete is simply repeated.

Retain and report: a database or object store that does not answer ends the pass with
what was not yet deleted kept. The claim TTL must exceed the object-store request timeout
(F2C.a); `run()` is the hook the composition root starts and I8 schedules.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections import Counter
from dataclasses import dataclass, field

from ..contracts import errors

log = logging.getLogger("infrx.media.retention")
#: Every object key M writes starts with one of these (store.py, uploads.py). A row naming
#: anything else is not deleted: a corrupt or foreign row must not reach the bucket.
DELETABLE_PREFIXES = ("media/", "uploads/", "payloads/")


def generation_key(object_key: str, generation: int) -> str:
    """Where generation `generation` of `object_key` lives. Generation 1 is the bare key
    (every object written before M6); a key re-created after an acknowledged delete gets a
    new physical name, so a delayed delete of an older generation cannot reach it."""
    return object_key if generation == 1 else f"{object_key}.g{generation}"


def deletable(object_key: str) -> bool:
    return object_key.startswith(DELETABLE_PREFIXES) and ".." not in object_key.split("/")


def _reason(refusal: errors.DomainError) -> str:
    return str(getattr(refusal, "refusal", None) or refusal.code)


@dataclass
class Report:
    """What one pass did, for the log line, the metrics and the tests."""

    pages: int = 0
    max_batch: int = 0                      # the largest page the store returned
    max_in_flight: int = 0                  # the most candidates handled at once
    deleted: list[tuple[str, str, int]] = field(default_factory=list)  # location, key, gen
    retained: Counter = field(default_factory=Counter)                 # refusal -> count
    delete_failed: int = 0
    ack_lost: int = 0
    max_pending_delete_s: float = 0.0       # oldest unfinished delete this pass finished
    aborted: str | None = None              # why the pass stopped early, if it did


class RetentionCollector:
    """Drives `lifecycle` (a `ContentLifecycle`) and deletes from `objects` (the M
    `ObjectStore`). `page_size` bounds each read, `concurrency` the candidates in flight."""

    def __init__(self, lifecycle, objects, *, page_size: int = 100, concurrency: int = 8,
                 holder: str | None = None) -> None:
        if page_size < 1 or concurrency < 1:
            raise ValueError("page_size and concurrency are positive")
        self.lifecycle, self.objects = lifecycle, objects
        self.page_size, self.concurrency = page_size, concurrency
        self.holder = holder or f"retention:{socket.gethostname()}:{os.getpid()}"
        self._in_flight = 0

    async def sweep(self) -> Report:
        report, cursor = Report(), None
        gate = asyncio.Semaphore(self.concurrency)

        async def one(item) -> None:
            async with gate:
                if report.aborted is None:
                    await self._collect(item, report)
        while report.aborted is None:
            try:
                page = await self.lifecycle.candidates(after=cursor, limit=self.page_size)
            except errors.DependencyUnavailable:
                report.aborted = "dependency_unavailable"
                break
            report.pages += 1
            report.max_batch = max(report.max_batch, len(page.items))
            await asyncio.gather(*(one(item) for item in page.items))
            cursor = page.next_cursor
            if cursor is None:
                break
        return report

    async def _collect(self, item, report: Report) -> None:
        self._in_flight += 1
        report.max_in_flight = max(report.max_in_flight, self._in_flight)
        try:
            await self._collect_one(item, report)
        finally:
            self._in_flight -= 1

    async def _collect_one(self, item, report: Report) -> None:
        identity = item.identity
        if identity.location == "object_store" and not deletable(identity.object_key):
            report.retained["foreign_key"] += 1
            return
        try:
            claim = await self.lifecycle.claim(item.content_id, item.generation, self.holder)
            if item.tombstoned_at is not None:          # an unfinished delete, taken over
                waited = (claim.claimed_at - item.tombstoned_at).total_seconds()
                report.max_pending_delete_s = max(report.max_pending_delete_s, waited)
            tombstone = await self.lifecycle.tombstone(claim)
        except errors.DependencyUnavailable:
            report.aborted = "dependency_unavailable"
            return
        except (errors.NotClaimable, errors.StaleLease, errors.NotFound) as refused:
            report.retained[_reason(refused)] += 1
            return
        in_database = tombstone.location == "database"
        try:
            if in_database:             # content-bearing columns: the store scrubs them (D3)
                await self.lifecycle.scrub(tombstone)
            else:
                await self.objects.delete(generation_key(tombstone.object_key,
                                                         tombstone.generation))
        except errors.DependencyUnavailable:
            report.delete_failed += 1
            report.aborted = "dependency_unavailable" if in_database \
                else "object_store_unavailable"
            return
        report.deleted.append((str(tombstone.location), tombstone.object_key,
                               tombstone.generation))
        try:
            await self.lifecycle.acknowledge_delete(tombstone)
        except errors.StaleLease:
            report.retained["claim_lost"] += 1          # a newer claim finishes it
        except errors.DependencyUnavailable:
            report.ack_lost += 1
            report.aborted = "dependency_unavailable"

    async def run(self, interval_s: float, *, sleep=asyncio.sleep) -> None:
        """The schedule hook: one pass every `interval_s`, forever. A failed pass is
        logged and the next one runs; nothing here decides to stop collecting."""
        while True:
            try:
                report = await self.sweep()
                log.info("retention sweep: %d deleted, %d retained, %d delete failures, "
                         "%d lost acks, batch %d, in flight %d, oldest pending delete %.0f s%s",
                         len(report.deleted), sum(report.retained.values()),
                         report.delete_failed, report.ack_lost, report.max_batch,
                         report.max_in_flight, report.max_pending_delete_s,
                         f", aborted: {report.aborted}" if report.aborted else "")
            except Exception:
                log.exception("retention sweep failed")
            await sleep(interval_s)
