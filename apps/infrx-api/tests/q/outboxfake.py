"""D2's dispatch outbox, in memory, until D2 merges.

`0012_dispatch_outbox.sql` at codex-d2 `30d614d` (D2 round 2, after OB-1b) defines the
dispatch functions `PgJobStore` wraps; this is each one, row for row, over the contract
`FakeJobStore`'s jobs and outbox list, keeping the columns the SQL writes (`claimed_at`,
`claimed_by`, `acknowledged_at`, `last_error`), so a Q3 case runs against the same rows
PostgreSQL will hand it:

* `dispatch_pending` - unacknowledged dispatch rows, `available_at <= now`, not claimed
  within `redelivery_s`, ordered by `(available_at, event_id)`, `limit` clamped to
  [1, 1000] BEFORE the row filter; each is claimed for `worker_id` if its job still wants
  it (`dispatch_wanted`) and acknowledged as `superseded` otherwise.
* `acknowledge_dispatch(event_ids, *, worker_id)` - newly acknowledged count, and only
  for rows this worker still claims (OB-1b): a released or reopened row answers 0.
* `dispatch_snapshot` - every `preparing`/`queued` job without a live lease of either
  kind (OB-5), with its latest wanted dispatch event, ordered by `(available_at,
  event_id)`.
* `release_dispatch` (OB-4) - clears the claim of unacknowledged rows: pending now.
* `record_dispatch_error` (SQL `fail_dispatch`, OB-7) - records `last_error`; the row
  stays pending and claimed.
* `db_now` / `reopen_dispatch(since)` (OB-1) - D2's rebuild fence: rows acknowledged or
  claimed at/after `since` whose job still wants them and holds no live lease are
  pending and unclaimed again.

Deliberately absent: `gc_outbox` and its tombstone window (0013). It deletes only
acknowledged rows of settled jobs, which no Q3 read can return (pending rows are
unacknowledged; the snapshot reads live jobs), and the rows are the contract fake's own
list. `attempts` is not counted: nothing reads it.

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


def leased(job, now) -> bool:
    """A live lease of either kind (OB-5): the job is being worked."""
    return any(lease is not None and lease.expires_at > now
               for lease in (job.preparation_lease, job.lease))


class FakeDispatchOutbox:
    def __init__(self, jobs) -> None:
        self.jobs = jobs                                  # contracts FakeJobStore
        self.claimed_at: dict[str, object] = {}
        self.claimed_by: dict[str, str] = {}
        self.acknowledged: dict[str, object] = {}         # event_id -> acknowledged_at
        self.last_error: dict[str, str] = {}
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

    def _unacked(self, event_ids) -> list[str]:
        kinds = {row.event_id: row.kind for row in self.jobs.outbox}
        return [event_id for event_id in dict.fromkeys(map(str, event_ids))
                if kinds.get(event_id) in DISPATCH_KINDS
                and event_id not in self.acknowledged]

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
                self.claimed_by[row.event_id] = worker_id
                self.deliveries[row.event_id] = self.deliveries.get(row.event_id, 0) + 1
                out.append(self._event(row, job))
            else:
                self.acknowledged[row.event_id] = now
                self.last_error[row.event_id] = "superseded"
        return tuple(out)

    async def acknowledge_dispatch(self, event_ids, *, worker_id: str) -> int:
        fault = self.ack_faults.pop(0) if self.ack_faults else None
        if fault == "before":
            raise OutboxLost("acknowledgment lost before commit")
        now = self.jobs.clock.now()
        fresh = [event_id for event_id in self._unacked(event_ids)
                 if event_id in self.claimed_at
                 and self.claimed_by.get(event_id) == worker_id]
        for event_id in fresh:
            self.acknowledged[event_id] = now
        if fault == "after":
            raise OutboxLost("acknowledgment committed, reply lost")
        return len(fresh)

    async def dispatch_snapshot(self) -> tuple[IndexEvent, ...]:
        self.snapshots += 1
        now = self.jobs.clock.now()
        out = []
        for job in self.jobs.jobs.values():
            if job.state not in (JobState.preparing, JobState.queued) or leased(job, now):
                continue
            latest = [row for row in self.jobs.outbox
                      if row.aggregate_id == job.id and wanted(row.kind, job.state)]
            if latest:
                out.append(self._event(latest[-1], job))
        return tuple(sorted(out, key=lambda event: (event.available_at, event.event_id)))

    async def release_dispatch(self, event_ids) -> int:
        released = self._unacked(event_ids)
        for event_id in released:
            self.claimed_at.pop(event_id, None)
            self.claimed_by.pop(event_id, None)
        return len(released)

    async def record_dispatch_error(self, event_id, error: str) -> int:
        failed = self._unacked([event_id])
        for event_id in failed:
            self.last_error[event_id] = (error or "unknown")[:500]
        return len(failed)

    async def db_now(self):
        return self.jobs.clock.now()

    async def reopen_dispatch(self, since) -> int:
        now = self.jobs.clock.now()
        reopened = 0
        for row in self.jobs.outbox:
            job = self.jobs.jobs.get(row.aggregate_id)
            if (row.kind not in DISPATCH_KINDS or job is None
                    or not wanted(row.kind, job.state) or leased(job, now)):
                continue
            acked, claimed = self.acknowledged.get(row.event_id), self.claimed_at.get(row.event_id)
            if (acked is not None and acked >= since) or (claimed is not None and claimed >= since):
                for column in (self.acknowledged, self.claimed_at, self.claimed_by):
                    column.pop(row.event_id, None)
                reopened += 1
        return reopened

    def unacknowledged(self) -> list[str]:
        return [row.event_id for row in self.jobs.outbox
                if row.kind in DISPATCH_KINDS and row.event_id not in self.acknowledged]
