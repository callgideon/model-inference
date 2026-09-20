"""Bounded in-memory TraceSink.

Trace capture is never an acceptance dependency, so nothing here blocks or fails a
request: it accepts into memory or drops with a counted reason.

r1 R27: content accumulates through a `TraceCapture`, and its bytes are charged
*while* they accumulate rather than when a finished envelope is offered. That is the
only accounting that holds when many requests capture at once: the budget is shared,
the breach belongs to whichever capture crosses it, and the whole of that capture's
content is discarded, because a partial capture must never look complete. `abandon`
gives its bytes back. The metadata reserve is what keeps metadata flowing after
content has been cut off.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from .. import errors
from ..limits import DEFAULTS, PilotSettings
from ..records import TraceEnvelope, TraceLossReason, TraceMode, TraceOfferResult
from .support import FailurePlan, FakeClock, failure_hooks


class FakeTraceCapture:
    """`ports.TraceCapture`: one request's accumulating content."""

    def __init__(self, sink: FakeTraceSink, request_id: str, org_id: str,
                 mode: TraceMode) -> None:
        self.sink = sink
        self.request_id = request_id
        self.org_id = org_id
        self.mode = mode
        self.content_bytes = 0
        self.lost_reason: TraceLossReason = TraceLossReason.none
        self.closed = False

    @property
    def lost(self) -> bool:
        return self.lost_reason is not TraceLossReason.none

    def add(self, part: bytes | str) -> bool:
        """Charge `part` to the shared capture budget.

        False means this capture is over: either it just breached the budget - in
        which case everything it had accumulated is discarded and the loss counted -
        or it was already lost or finished.
        """
        if self.closed or self.lost:
            return False
        size = len(part.encode() if isinstance(part, str) else part)
        if self.sink.content_bytes + size > self.sink.content_budget:
            self._discard(TraceLossReason.memory_budget)
            return False
        self.sink.content_bytes += size
        self.content_bytes += size
        return True

    def _discard(self, reason: TraceLossReason) -> None:
        self.sink.content_bytes -= self.content_bytes
        self.content_bytes = 0
        self.lost_reason = reason
        self.sink.loss_reasons[reason] += 1

    async def finish(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """Hand the completed capture to the bounded queue. A capture that lost its
        content finishes as honest metadata carrying the loss reason."""
        if self.closed:
            raise errors.InvalidRequest("this capture is already finished")
        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):
            raise errors.InvalidRequest("the envelope does not belong to this capture")
        self.closed = True
        charged = self.content_bytes
        if self.lost or envelope.content_bytes == 0:
            envelope = envelope.model_copy(update={
                "content_complete": False, "content_ref": None, "content_bytes": 0,
                "loss_reason": self.lost_reason})
            self.sink.content_bytes -= charged      # already released if lost, so net 0
            self.content_bytes = charged = 0
        return self.sink._enqueue(envelope, charged=charged)

    async def abandon(self, reason: TraceLossReason = TraceLossReason.shutdown) -> None:
        """Give the bytes back: an abandoned capture must not hold budget a live
        request could be using."""
        if self.closed:
            return
        self.closed = True
        if not self.lost:
            self._discard(reason)


class FakeTraceSink:
    """`ports.TraceSink`."""

    def __init__(self, clock: FakeClock | None = None, *, limits: PilotSettings = DEFAULTS,
                 failures: FailurePlan | None = None) -> None:
        self.clock = clock or FakeClock()
        self.limits = limits
        self.failures = failure_hooks(failures)
        self.queued: list[TraceEnvelope] = []
        self.content_bytes = 0
        self.metadata_bytes = 0
        self.accepted = 0
        self.dropped = 0
        self.loss_reasons: Counter = Counter()
        self.appended: list[TraceEnvelope] = []
        self.fsynced: list[TraceEnvelope] = []
        self.captures: list[FakeTraceCapture] = []
        self._last_fsync: datetime = self.clock.now()

    @property
    def content_budget(self) -> int:
        """Content shares the process budget with the metadata reserve."""
        return self.limits.trace_capture_bytes - self.limits.trace_metadata_reserve_bytes

    def open(self, request_id: str, org_id: str, mode: TraceMode) -> FakeTraceCapture:
        """r1 R27: only a `full`-mode request accumulates content. `off` produces no
        row at all and `minimal` is metadata only (R12), so neither opens a capture:
        calling this for one is a caller bug, not a silent privacy downgrade."""
        self.failures.before("open")
        if mode is not TraceMode.full:
            raise errors.InvalidRequest(f"{mode} mode never opens a content capture")
        capture = FakeTraceCapture(self, request_id, org_id, mode)
        self.captures.append(capture)
        return capture

    async def offer(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """Metadata-only envelopes (r1 R27); content arrives through `open`."""
        self.failures.before("offer")
        if envelope.mode is TraceMode.off:
            # r1 R27: an off-mode request has no trace row. Offering one is a caller
            # bug, but the trace path never raises into the request path: it is
            # dropped and counted, which is also how the bug becomes visible.
            return self._drop(TraceLossReason.malformed)
        if envelope.mode is not TraceMode.full and envelope.carries_content:
            # r1 R12: a minimal-mode envelope never carries content. The record model
            # refuses to build one, so this only fires for an envelope an adapter
            # assembled from raw bytes; it is dropped whole as malformed rather than
            # queued with unconsented, uncharged content.
            return self._drop(TraceLossReason.malformed)
        return self._enqueue(envelope, charged=0)

    def _enqueue(self, envelope: TraceEnvelope, *, charged: int) -> TraceOfferResult:
        """The bounded queue. `charged` is what a capture already accounted for, so
        finishing one never counts its bytes twice."""
        if len(self.queued) >= self.limits.trace_queue_max:
            self.content_bytes -= charged
            return self._drop(TraceLossReason.queue_full)
        if self.metadata_bytes + envelope.metadata_bytes > self.limits.trace_metadata_reserve_bytes:
            self.content_bytes -= charged
            return self._drop(TraceLossReason.metadata_budget)
        uncharged = envelope.content_bytes - charged
        if uncharged > 0:
            # Content that never went through a capture still has to fit the budget,
            # or `offer` would be a way around the accounting altogether.
            if self.content_bytes + uncharged > self.content_budget:
                envelope = envelope.model_copy(update={
                    "content_complete": False, "content_ref": None, "content_bytes": 0,
                    "loss_reason": TraceLossReason.memory_budget})
                self.loss_reasons[TraceLossReason.memory_budget] += 1
            else:
                self.content_bytes += uncharged
        self.metadata_bytes += envelope.metadata_bytes
        self.queued.append(envelope)
        self.accepted += 1
        return TraceOfferResult.accepted_in_memory

    def _drop(self, reason: TraceLossReason) -> TraceOfferResult:
        self.dropped += 1
        self.loss_reasons[reason] += 1
        return TraceOfferResult.dropped

    async def stats(self) -> dict[str, object]:
        """In-memory, appended and fsynced are reported separately: durability
        begins at fsync, and nothing here claims it earlier."""
        return {
            "accepted": self.accepted,
            "dropped": self.dropped,
            "in_memory": len(self.queued),
            "in_memory_content_bytes": self.content_bytes,
            "in_memory_metadata_bytes": self.metadata_bytes,
            "open_captures": len([capture for capture in self.captures if not capture.closed]),
            "appended": len(self.appended),
            "fsynced": len(self.fsynced),
            "loss_reasons": {reason.value: count for reason, count in self.loss_reasons.items()},
        }

    async def flush(self, deadline: datetime | None = None) -> dict[str, object]:
        """Append everything held in memory; fsync only when the batch interval has
        elapsed, so appended-but-not-fsynced is an observable state."""
        self.failures.before("flush")
        self.appended.extend(self.queued)
        self.queued.clear()
        # Bytes held by captures that have not finished stay charged: they are still
        # accumulating, and flushing the queue does not make their content free.
        self.content_bytes = sum(capture.content_bytes for capture in self.captures
                                 if not capture.closed)
        self.metadata_bytes = 0
        now = self.clock.now()
        if (now - self._last_fsync).total_seconds() >= self.limits.trace_fsync_interval_s:
            self.fsynced.extend(env for env in self.appended if env not in self.fsynced)
            self._last_fsync = now
        return await self.stats()

    def crash(self) -> int:
        """Process loss: appended but unsynced records are gone. Only fsynced
        records were ever promised to survive."""
        lost = [env for env in self.appended if env not in self.fsynced]
        self.appended = list(self.fsynced)
        self.queued.clear()
        self.captures.clear()
        self.content_bytes = self.metadata_bytes = 0
        self.loss_reasons[TraceLossReason.shutdown] += len(lost)
        return len(lost)
