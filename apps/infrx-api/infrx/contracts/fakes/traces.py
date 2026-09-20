"""Bounded in-memory TraceSink.

Trace capture is never an acceptance dependency, so `offer` cannot block and
cannot fail the request: it accepts into memory or drops with a counted reason.
Bytes are charged while content is accumulating, a content-budget breach discards
the whole incomplete capture (a partial capture must never look complete), and the
metadata reserve is what keeps metadata flowing after content is cut off.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from ..limits import DEFAULTS, PilotSettings
from ..records import TraceEnvelope, TraceLossReason, TraceMode, TraceOfferResult
from .support import FailurePlan, FakeClock, failure_hooks


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
        self._last_fsync: datetime = self.clock.now()

    @property
    def content_budget(self) -> int:
        """Content shares the process budget with the metadata reserve."""
        return self.limits.trace_capture_bytes - self.limits.trace_metadata_reserve_bytes

    async def offer(self, envelope: TraceEnvelope) -> TraceOfferResult:
        self.failures.before("offer")
        if envelope.mode is TraceMode.off:
            # Off-mode requests produce no trace at all; offering one is a caller bug.
            raise ValueError("off-mode requests are never offered to the sink")
        if len(self.queued) >= self.limits.trace_queue_max:
            return self._drop(TraceLossReason.queue_full)
        if self.metadata_bytes + envelope.metadata_bytes > self.limits.trace_metadata_reserve_bytes:
            return self._drop(TraceLossReason.metadata_budget)

        keep_content = envelope.mode is TraceMode.full and envelope.content_bytes > 0
        if keep_content and self.content_bytes + envelope.content_bytes > self.content_budget:
            # Discard the whole content capture; keep honest metadata.
            envelope = envelope.model_copy(update={
                "content_complete": False, "content_ref": None, "content_bytes": 0,
                "loss_reason": TraceLossReason.memory_budget})
            self.loss_reasons[TraceLossReason.memory_budget] += 1
            keep_content = False
        if keep_content:
            self.content_bytes += envelope.content_bytes
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
        self.content_bytes = 0
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
        self.content_bytes = self.metadata_bytes = 0
        self.loss_reasons[TraceLossReason.shutdown] += len(lost)
        return len(lost)
