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
from datetime import datetime, timedelta

from .. import errors
from ..limits import DEFAULTS, PilotSettings
from ..records import TraceEnvelope, TraceLossReason, TraceMode, TraceOfferResult
from .support import FailurePlan, FakeClock, failure_hooks


class FakeTraceCapture:
    """`ports.TraceCapture`: one request's accumulating content.

    r1 R37: nothing here raises into the request path, `finish` and `abandon` are
    idempotent, and the object is a context manager whose exit abandons an unfinished
    capture - G can use it in a `finally` without a second thought.
    """

    def __init__(self, sink: FakeTraceSink, request_id: str, org_id: str, mode: TraceMode,
                 *, deadline_at: datetime | None = None, no_op: bool = False) -> None:
        self.sink = sink
        self.request_id = request_id
        self.org_id = org_id
        self.mode = mode
        self.deadline_at = deadline_at
        self.no_op = no_op          # off/minimal: accepts calls, keeps nothing
        self.content_bytes = 0
        self.lost_reason: TraceLossReason = TraceLossReason.none
        self.closed = False
        self.finished = False
        # A capture contributes **at most one** loss count however it ends: abandoned
        # then finished, breached then reaped, exited twice - one record, one count.
        self.counted = False
        self.result: TraceOfferResult | None = None

    def __enter__(self) -> FakeTraceCapture:
        return self

    def __exit__(self, *exc_info) -> bool:
        """Exit abandons an unfinished capture and swallows nothing: the request's own
        exception (if any) propagates, the capture's bytes do not leak."""
        if not self.closed:
            self._close(TraceLossReason.abandoned)
        return False

    @property
    def lost(self) -> bool:
        return self.lost_reason is not TraceLossReason.none

    def _count(self, reason: TraceLossReason) -> None:
        """Record this capture's **single** loss (r1 R42).

        Idempotent per capture, deliberately: the guard belongs here rather than at each
        call site, because every route that ends a capture - a budget breach, a malformed
        part, `abandon`, a context exit, `reap`, a late `finish`, and whatever route is
        added next - must be unable to count twice. r7 moved the guard to the callers and
        two routes promptly double counted (`abandon` then `finish`; a breach then a
        mode-mismatched `finish`), which is exactly the class this is now closed against.
        """
        if self.counted:
            return
        self.counted = True
        self.sink.loss_reasons[reason] += 1

    def add(self, part: bytes | str) -> bool:
        """Charge `part` to the shared capture budget.

        Synchronous, O(1) in the number of open captures, no disk, no lock beyond the
        counter update (r1 R37), and never an exception into the request path. False
        means this capture is over: it just breached the budget - in which case
        everything it had accumulated is discarded and the loss counted - or it was
        already lost, finished or a no-op.
        """
        if self.no_op or self.closed or self.lost:
            return False
        if isinstance(part, str):
            part = part.encode()
        elif not isinstance(part, (bytes, bytearray, memoryview)):
            # Not content: dropped and counted, never a `TypeError` into the request
            # path (R37). The capture is over, as it is for any other malformed input.
            self._discard(TraceLossReason.malformed)
            return False
        size = len(part)
        if self.sink.content_bytes + size > self.sink.content_budget:
            self._discard(TraceLossReason.memory_budget)
            return False
        self.sink.content_bytes += size
        self.content_bytes += size
        return True

    def _discard(self, reason: TraceLossReason) -> None:
        # `max(0, ...)`: a crash clears the sink's counter under the captures' feet, and
        # a later abandon must not drive it negative (R37).
        self.sink.content_bytes = max(0, self.sink.content_bytes - self.content_bytes)
        self.content_bytes = 0
        self.lost_reason = reason
        self._count(reason)

    async def finish(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """Hand the completed capture to the bounded queue. A capture that lost its
        content finishes as honest metadata carrying the loss reason.

        Idempotent (R37): finishing twice queues one record and returns the first
        result, and a mismatched envelope is *dropped and counted*, never raised - this
        runs where a request is finishing, and a trace bug may not become its error.
        """
        if self.result is not None:
            # Idempotent, and the answer is the **first** one: a caller retrying after a
            # drop must not be told the record was accepted.
            return self.result
        if self.no_op:
            result = self._finish_no_op(envelope)
            self.closed = self.finished = True
            self.result = result
            return self.result
        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):
            # The single loss this capture contributes is labelled by what went wrong:
            # the envelope did not belong to it, which is `malformed`, not `abandoned`.
            self._close(TraceLossReason.malformed)
            self.result = self.sink._drop(TraceLossReason.malformed, counted=True)
            return self.result
        if self.closed:                      # abandoned or reaped first: nothing to queue
            self.finished = True
            # `TraceLossReason.none` is the truthy string "none", so `or` never fell
            # through here: a drop was once counted under `none`, which is not a loss
            # reason at all.
            reason = (TraceLossReason.abandoned if self.lost_reason is TraceLossReason.none
                      else self.lost_reason)
            self.result = self.sink._drop(reason, counted=self.counted)
            return self.result
        self.closed = self.finished = True
        charged = self.content_bytes
        if envelope.mode is not self.mode:
            # The capture decides, on this path as on the no-op one: an envelope whose
            # mode is not the mode this capture was opened with would file a mislabelled
            # row - `off` or `minimal` over content that really was captured, or `full`
            # over a request that never consented to content. Dropped, charge released,
            # and counted only if this capture has not already contributed its one loss
            # (a breach or a malformed part may have got there first).
            self._discard(TraceLossReason.malformed)
            self.result = self.sink._drop(TraceLossReason.malformed, counted=True)
            return self.result
        if not self.lost and envelope.content_bytes > charged:
            # The envelope claims more content than this capture charged, so those bytes
            # never went through the budget: the numbers must agree, or `add` is
            # decoration. (A *lost* capture is the honest exception - its charge was
            # released on purpose and its envelope is stripped below.)
            self._discard(TraceLossReason.malformed)
            self.result = self.sink._drop(TraceLossReason.malformed, counted=True)
            return self.result
        if self.lost or envelope.content_bytes == 0:
            envelope = envelope.model_copy(update={
                "content_complete": False, "content_ref": None, "content_bytes": 0,
                "loss_reason": self.lost_reason})
            self.sink.content_bytes = max(0, self.sink.content_bytes - charged)
            self.content_bytes = charged = 0
        self.result = self.sink._enqueue(envelope, charged=charged, capture=self)
        return self.result

    def _finish_no_op(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """r1 R37/R12 + 01: what a no-op capture does with the envelope it is handed.

        The **capture** decides, not the envelope: a capture opened `minimal` cannot be
        talked into queueing content by an envelope that calls itself `full`, and a
        capture opened `off` queues nothing whatever arrives. Trusting the envelope here
        would make the mode a client-supplied field, which is exactly what R12 forbids.
        """
        if self.mode is TraceMode.off:
            # r1 R42: an off-mode capture is **silent**. 02 keeps off-mode jobs out of the
            # loss and coverage figures, and G is told not to branch on the mode, so the
            # ordinary off-mode lifecycle must not look like an anomaly: nothing is stored,
            # no loss is counted and the `dropped` counter does not move. `dropped` is the
            # honest *result* - nothing was stored - but it is not a dropped *record*.
            # This is checked before the closed branch below, because an off capture is
            # silent in every state: abandoned-then-finished must count nothing either.
            return TraceOfferResult.dropped
        if self.closed:
            # Already ended - abandoned, reaped, or finished. Its single loss (if any) is
            # counted, so this call queues nothing and adds nothing; it answers what the
            # first call answered (r1 R42; G's `finally` abandoning before a late `finish`
            # is the ordinary order, not an edge case).
            return self.result if self.result is not None else self.sink._drop(
                self.lost_reason if self.lost_reason is not TraceLossReason.none
                else TraceLossReason.abandoned, counted=True)
        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):
            # The same identity check the accumulating path makes: one request's envelope
            # must never be filed under another's - or another tenant's.
            self._count(TraceLossReason.malformed)
            return self.sink._drop(TraceLossReason.malformed, counted=True)
        if self.mode is TraceMode.minimal:
            # Metadata only, and it must say so: an envelope claiming another mode, or
            # carrying content, is a caller bug that would store unconsented content.
            if envelope.mode is not TraceMode.minimal or envelope.carries_content:
                self._count(TraceLossReason.malformed)
                return self.sink._drop(TraceLossReason.malformed, counted=True)
            return self.sink._enqueue(envelope, charged=0, capture=self)
        # A `full` capture that is a no-op because it was opened without a deadline: its
        # content was never accumulated (every `add` returned False), so it finishes as
        # honest metadata with exactly one counted loss. `abandoned` is the reason: the
        # sink gave up on a capture it could never have reaped, which is the same fact
        # `reap` records - and 02 requires the loss to be marked *and* counted, so
        # `loss_reason: none` is not an option.
        self.lost_reason = TraceLossReason.abandoned
        self._count(TraceLossReason.abandoned)
        return self.sink._enqueue(envelope.model_copy(update={
            "content_complete": False, "content_ref": None, "content_bytes": 0,
            "loss_reason": TraceLossReason.abandoned}), charged=0, capture=self)

    async def abandon(self, reason: TraceLossReason = TraceLossReason.abandoned) -> None:
        """Give the bytes back: an abandoned capture must not hold budget a live request
        could be using. Idempotent, and never raises (R37)."""
        self._close(reason)

    def _close(self, reason: TraceLossReason) -> None:
        """End the capture, counting its single loss if it has not been counted yet.

        A no-op capture counts one too: a `full`-mode request whose trace was never
        capturable (no deadline) has lost its content just as surely as one that breached
        the budget, and 02 wants every loss marked *and* counted. An `off` or `minimal`
        capture has no content to lose, so there is nothing to count.
        """
        if self.closed:
            return
        self.closed = True
        if self.lost:
            return
        if self.no_op and self.mode is not TraceMode.full:
            # r1 R42: `off` and `minimal` captures hold no content, so ending one is not a
            # loss and must not be counted - an off-mode request is out of the loss and
            # coverage figures entirely (02).
            return
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

    def open(self, request_id: str, org_id: str, mode: TraceMode,
             deadline_at: datetime | None = None) -> FakeTraceCapture:
        """r1 R27/R37: only a `full`-mode request accumulates content. `off` produces no
        row at all and `minimal` is metadata only (R12), so both get a **no-op
        capture**: `add` returns False, `finish` keeps nothing, and the caller needs no
        branch. Nothing here raises into the request path.

        `deadline_at` is the job's absolute deadline; the sink reaps captures that are
        still open past it (plus a grace period) and counts them `abandoned`.
        """
        self.failures.before("open")
        # r1 R37: `deadline_at` is what makes a capture reapable. Without one the sink
        # could never release its bytes, so it gets a no-op capture instead of a leak.
        no_op = mode is not TraceMode.full or deadline_at is None
        capture = FakeTraceCapture(self, request_id, org_id, mode, deadline_at=deadline_at,
                                   no_op=no_op)
        self.captures.append(capture)
        return capture

    def reap(self, grace_s: float = 60.0) -> int:
        """r1 R37: release the bytes of captures still open past their job's deadline.

        A request that dies without its `finally` running - a killed process, a lost
        connection mid-stream - would otherwise hold capture budget until the process
        restarted. Counted under `abandoned`, never charged to the customer.
        """
        now = self.clock.now()
        grace_s = max(0.0, grace_s)             # a negative grace never reaps live work
        reaped = 0
        for capture in self.captures:
            if capture.closed or capture.deadline_at is None:
                continue
            if now >= capture.deadline_at + timedelta(seconds=grace_s):
                capture._close(TraceLossReason.abandoned)
                reaped += 1
        return reaped

    async def offer(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """Metadata-only envelopes (r1 R27); content arrives through `open`.

        An off-mode envelope has no trace row (01) and a minimal one never carries
        content (R12): both are dropped and counted `malformed`, because the trace path
        never raises into the request path - and counting is how the caller's bug
        becomes visible.
        """
        self.failures.before("offer")
        if envelope.carries_content:
            # r1 R27: content is charged as it accumulates, through `open`/`add`. An
            # envelope arriving here with content was never charged to the budget, so
            # queueing it would be an unaccounted capture; it is dropped and counted.
            return self._drop(TraceLossReason.malformed)
        return self._offer_metadata(envelope)

    def _offer_metadata(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """`offer` semantics: an off-mode request has no row (01). Content is refused by
        `offer` itself and by a capture's `finish`, so there is no check for it here."""
        if envelope.mode is TraceMode.off:
            return self._drop(TraceLossReason.malformed)
        return self._enqueue(envelope, charged=0)

    def _enqueue(self, envelope: TraceEnvelope, *, charged: int,
                 capture: FakeTraceCapture | None = None) -> TraceOfferResult:
        """The bounded queue. `charged` is what a capture already accounted for, so
        finishing one never counts its bytes twice - and **every** path that drops the
        record gives those bytes back, or the budget would leak one drop at a time."""
        for reason in (TraceLossReason.queue_full if len(self.queued) >= self.limits.trace_queue_max
                       else None,
                       TraceLossReason.metadata_budget
                       if self.metadata_bytes + envelope.metadata_bytes
                       > self.limits.trace_metadata_reserve_bytes else None):
            if reason is None:
                continue
            self.content_bytes = max(0, self.content_bytes - charged)
            if capture is not None:
                capture.content_bytes = 0
            return self._drop(reason, counted=capture.counted if capture else False)
        # No "uncharged content" branch: content reaches the queue only through a
        # capture, whose `finish` refuses an envelope claiming more than it charged, and
        # `offer` refuses content outright. One rule, in one place each.
        self.metadata_bytes += envelope.metadata_bytes
        self.queued.append(envelope)
        self.accepted += 1
        return TraceOfferResult.accepted_in_memory

    def _drop(self, reason: TraceLossReason, *, counted: bool = False) -> TraceOfferResult:
        """One dropped record. `counted=True` means this capture's loss is already in
        `loss_reasons`, so the drop is recorded without counting the loss twice."""
        assert reason is not TraceLossReason.none, "a drop always has a reason"
        self.dropped += 1
        if not counted:
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
        """Process loss: appended but unsynced records are gone. Only fsynced records
        were ever promised to survive. Open captures die with the process; their bytes
        go with them, and the counters cannot go negative afterwards (R37)."""
        lost = [env for env in self.appended if env not in self.fsynced]
        self.appended = list(self.fsynced)
        self.queued.clear()
        for capture in self.captures:
            capture.closed = True
            capture.content_bytes = 0
        self.captures.clear()
        self.content_bytes = self.metadata_bytes = 0
        self.loss_reasons[TraceLossReason.shutdown] += len(lost)
        return len(lost)
