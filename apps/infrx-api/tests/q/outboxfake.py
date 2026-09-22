"""D2's dispatch outbox, in memory, until D2 merges.

`0012_dispatch_outbox.sql` (codex-d2 @ 8728aec) defines three functions the Q3
reconciler reads; this is each of them, row for row, over the contract `FakeJobStore`'s
jobs and outbox list, so a Q3 case runs against the same rows PostgreSQL will hand it:

* `dispatch_pending`  - unacknowledged dispatch rows, `available_at <= now`, not claimed
  within `redelivery_s`, ordered by `(available_at, event_id)`, `limit` clamped to
  [1, 1000]; each is stamped claimed if its job still wants it (`dispatch_wanted`) and
  acknowledged as `superseded` otherwise.
* `acknowledge_dispatch` - newly acknowledged count; idempotent.
* `dispatch_snapshot` - every `preparing`/`queued` job without a live preparation lease,
  with its latest wanted dispatch event, ordered by `(available_at, event_id)`.

`ack_faults` injects the two ways an acknowledgment is lost: `"before"` (the write never
commits) and `"after"` (it commits and the reply is lost).
"""
from __future__ import annotations

from datetime import timedelta

from infrx.contracts.records import DISPATCH_KINDS, IndexEvent, JobState, OutboxKind


class OutboxLost(ConnectionError):
    """The connection died around an acknowledgment."""


def wanted(kind: OutboxKind, state: JobState) -> bool:
    return ((kind is OutboxKind.prepare_dispatch and state is JobState.preparing)
            or (kind is OutboxKind.inference_dispatch and state is JobState.queued))


class FakeDispatchOutbox:
    def __init__(self, jobs) -> None:
        self.jobs = jobs                                  # contracts FakeJobStore
        self.claimed_at: dict[str, object] = {}
        self.acknowledged: dict[str, str | None] = {}     # event_id -> last_error
        self.deliveries: dict[str, int] = {}              # event_id -> times handed out
        self.ack_faults: list[str] = []
        self.snapshots = 0

    def _event(self, row, job) -> IndexEvent:
        return IndexEvent(event_id=row.event_id, job_id=job.id, org_id=job.request.org_id,
                          key_id=job.request.key_id, kind=row.kind,
                          execution_mode=job.request.execution_mode,
                          available_at=row.available_at,
                          attempt=(job.preparation_attempts if row.kind
                                   is OutboxKind.prepare_dispatch else job.attempts))

    async def dispatch_pending(self, *, limit: int = 100, worker_id: str = "relay",
                               redelivery_s: float = 30.0) -> tuple[IndexEvent, ...]:
        now = self.jobs.clock.now()
        due = now - timedelta(seconds=redelivery_s)
        rows = sorted((row for row in self.jobs.outbox
                       if row.kind in DISPATCH_KINDS and row.event_id not in self.acknowledged
                       and row.available_at <= now
                       and (row.event_id not in self.claimed_at
                            or self.claimed_at[row.event_id] <= due)),
                      key=lambda row: (row.available_at, row.event_id))
        out = []
        for row in rows[:max(1, min(limit, 1000))]:
            job = self.jobs.jobs[row.aggregate_id]
            if wanted(row.kind, job.state):
                self.claimed_at[row.event_id] = now
                self.deliveries[row.event_id] = self.deliveries.get(row.event_id, 0) + 1
                out.append(self._event(row, job))
            else:
                self.acknowledged[row.event_id] = "superseded"
        return tuple(out)

    async def acknowledge_dispatch(self, event_ids) -> int:
        fault = self.ack_faults.pop(0) if self.ack_faults else None
        if fault == "before":
            raise OutboxLost("acknowledgment lost before commit")
        kinds = {row.event_id: row.kind for row in self.jobs.outbox}
        fresh = [event_id for event_id in dict.fromkeys(map(str, event_ids))
                 if kinds.get(event_id) in DISPATCH_KINDS
                 and event_id not in self.acknowledged]
        for event_id in fresh:
            self.acknowledged[event_id] = None
        if fault == "after":
            raise OutboxLost("acknowledgment committed, reply lost")
        return len(fresh)

    async def dispatch_snapshot(self) -> tuple[IndexEvent, ...]:
        self.snapshots += 1
        now = self.jobs.clock.now()
        out = []
        for job in self.jobs.jobs.values():
            if job.state not in (JobState.preparing, JobState.queued):
                continue
            lease = job.preparation_lease
            if lease is not None and lease.expires_at > now:
                continue
            latest = [row for row in self.jobs.outbox
                      if row.aggregate_id == job.id and wanted(row.kind, job.state)]
            if latest:
                out.append(self._event(latest[-1], job))
        return tuple(sorted(out, key=lambda event: (event.available_at, event.event_id)))

    def unacknowledged(self) -> list[str]:
        return [row.event_id for row in self.jobs.outbox
                if row.kind in DISPATCH_KINDS and row.event_id not in self.acknowledged]
