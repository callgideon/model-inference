"""In-memory Scheduler: an index, and nothing more.

The one invariant Q must never break is here: claiming a candidate changes no
durable state. Execution is authorized by `JobStore.claim` alone, so losing this
index loses throughput, never jobs, and replaying it duplicates nothing.
"""
from __future__ import annotations

from datetime import timedelta

from ..limits import DEFAULTS, PilotSettings
from ..records import IndexEvent
from .support import FailurePlan, FakeClock, failure_hooks


class FakeScheduler:
    """`ports.Scheduler`."""

    def __init__(self, clock: FakeClock | None = None, *, limits: PilotSettings = DEFAULTS,
                 failures: FailurePlan | None = None) -> None:
        self.clock = clock or FakeClock()
        self.limits = limits
        self.failures = failure_hooks(failures)
        self.pending: dict[str, IndexEvent] = {}        # event_id -> event
        self.inflight: dict[str, tuple[IndexEvent, str]] = {}
        self.acknowledged: set[str] = set()

    async def enqueue(self, event: IndexEvent) -> bool:
        """Replay safe: the same stable event id indexes exactly one candidate."""
        self.failures.before("enqueue")
        if event.event_id in self.pending or event.event_id in self.inflight \
                or event.event_id in self.acknowledged:
            return False
        self.pending[event.event_id] = event
        return True

    async def claim_candidate(self, worker_id: str) -> IndexEvent | None:
        """A hint, not an authorization. Visibility times out so a lost worker's
        candidate returns to the index without touching PostgreSQL."""
        self.failures.before("claim_candidate")
        now = self.clock.now()
        for event_id, (event, owner) in list(self.inflight.items()):
            if now >= event.available_at + timedelta(seconds=self.limits.lease_ttl_s):
                del self.inflight[event_id]
                self.pending[event_id] = event
        for event_id, event in sorted(self.pending.items(),
                                      key=lambda item: (item[1].available_at, item[0])):
            if event.available_at <= now:
                del self.pending[event_id]
                self.inflight[event_id] = (event, worker_id)
                return event
        return None

    async def acknowledge(self, event: IndexEvent) -> None:
        self.failures.before("acknowledge")
        self.inflight.pop(event.event_id, None)
        self.pending.pop(event.event_id, None)
        self.acknowledged.add(event.event_id)

    async def remove(self, job_id: str) -> None:
        self.failures.before("remove")
        for store in (self.pending, self.inflight):
            for event_id, value in list(store.items()):
                event = value[0] if isinstance(value, tuple) else value
                if event.job_id == job_id:
                    del store[event_id]

    async def rebuild(self, snapshot: tuple[IndexEvent, ...]) -> int:
        """Rebuild from PostgreSQL truth. Disabling or losing the index is not a
        data migration: every queued job comes back, exactly once."""
        self.failures.before("rebuild")
        self.pending = {event.event_id: event for event in snapshot}
        self.inflight.clear()
        self.acknowledged.clear()
        return len(self.pending)

    def depth(self) -> int:
        return len(self.pending) + len(self.inflight)
