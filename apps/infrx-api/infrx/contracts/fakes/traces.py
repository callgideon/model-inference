"""Bounded in-memory TraceSink: the shared accounting plus the fake's test hooks.

The accounting (R27/R37/R42) is `contracts.traces_accounting`, which the production spool
sink subclasses too. What is added here is what only a test double may have: a default
`FakeClock`, the harness `FailurePlan` hooks, in-memory "durability" lists and `crash()`.
"""
from __future__ import annotations

from datetime import datetime

from ..limits import DEFAULTS, PilotSettings
from ..records import TraceEnvelope, TraceLossReason, TraceMode, TraceOfferResult
from ..traces_accounting import TraceCaptureBase, TraceSinkBase
from .support import FailurePlan, FakeClock, failure_hooks

#: The accounting capture, under the name the fake has always exported.
FakeTraceCapture = TraceCaptureBase


class FakeTraceSink(TraceSinkBase):
    """`ports.TraceSink`."""

    def __init__(self, clock: FakeClock | None = None, *, limits: PilotSettings = DEFAULTS,
                 failures: FailurePlan | None = None) -> None:
        super().__init__(clock or FakeClock(), limits=limits)
        self.failures = failure_hooks(failures)

    def open(self, request_id: str, org_id: str, mode: TraceMode,
             deadline_at: datetime | None = None) -> TraceCaptureBase:
        self.failures.before("open")
        return super().open(request_id, org_id, mode, deadline_at)

    async def offer(self, envelope: TraceEnvelope) -> TraceOfferResult:
        self.failures.before("offer")
        return await super().offer(envelope)

    async def flush(self, deadline: datetime | None = None) -> dict[str, object]:
        self.failures.before("flush")
        return await super().flush(deadline)

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
        if lost:
            # Only a real loss names a reason. `loss_reasons` is a defaultdict, so
            # incrementing by zero *created* a `shutdown: 0` entry, and a crash with
            # nothing to lose - every off-mode process among them - then reported a loss
            # table where R42 requires silence.
            self.loss_reasons[TraceLossReason.shutdown] += len(lost)
        return len(lost)
