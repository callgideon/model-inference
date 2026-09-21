"""Q1: the deterministic in-process scheduling index.

`ports.Scheduler`, in memory. The one invariant this module must never break is the
one the fake states: **membership never authorizes execution**. A candidate is a hint
that some job wants something done; the right to execute it comes from
`JobStore.claim`/`claim_preparation` alone, so losing this index loses throughput,
never jobs, and replaying it duplicates nothing.

What it adds over the fake (which is a FIFO by design, because it is the executable
*specification* of the port, not the dispatcher):

* **Per-tenant weighted fair selection.** One flow per `(dispatch kind, org)`, FIFO
  inside a flow, start-time fair queuing across flows. `research/production-api/03`
  §2.3 argues for WFQ *by service time* rather than request count; its Lua sketch
  advances the tenant's virtual finish time at *enqueue*, which lets a tenant that
  enqueues a burst and then cancels it carry a penalty it never consumed. The tag is
  advanced **at dispatch** here, so it measures service actually handed out.
* **Virtual time, not wall time.** The tag of the flow being dispatched becomes the
  index's virtual time, and a flow that arrives (or comes back) takes that value.
  A tenant therefore accumulates no credit while idle and cannot hoard it, and none of
  it depends on how fast the clock moves. The injected clock is used only for
  `available_at` and for visibility timeouts, which are real durations.
* **Separate streams per dispatch kind.** R52: preparation and inference are
  different worker pools, so they are different flows with their own tags. A tenant's
  transcodes never push it back in the GPU line, and `kind=None` still takes whatever
  is next across both.
* **Bounded memory.** Queued-item and queued-byte caps (`03` §2.5, which takes them
  from llm-d's `maxRequests`/`maxBytes`), because an index is host memory. The cap is
  *not* admission capacity: PostgreSQL admits, and a refused index write is a retry
  for the outbox drain, never a lost job.

Determinism: no wall clock, no randomness, no set/dict iteration order in any
decision. Selection orders flows by `(tag, arrival sequence of the candidate)`, which
is a total order, so identical input gives an identical dispatch order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Mapping

from ..contracts import errors
from ..contracts.codec import compact_bytes
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import DISPATCH_KINDS, IndexEvent, OutboxKind

# `research/production-api/03` §2.5: host-memory bounds, not admission capacity
# (llm-d's `maxRequests: 200` / `maxBytes: 10Gi` scaled to one gateway process).
# They are constructor arguments because `PilotSettings` has no field for them yet;
# the coordinator request is in the Q1 evidence report.
MAX_INDEX_ITEMS = 500
MAX_INDEX_BYTES = 268_435_456                     # 256 MiB


def _one_second(_event: IndexEvent) -> float:
    """Provisional service-time estimate, in seconds, for WFQ.

    `03` §2.3 wants predicted service seconds (Marlin prompts vary ~11x), and §2.4's
    measured EWMA does not exist yet. A constant makes the fair share per request
    rather than per second; it is injected so Q3 can pass the real estimator without
    touching this file, and with equal costs the algorithm degenerates to weighted
    round robin, which is the conservative choice, not a wrong one.
    """
    return 1.0


@dataclass
class _Entry:
    """One indexed candidate: pending while `claimed_at is None`, else in flight."""

    event: IndexEvent
    seq: int                                      # arrival order; ties break on it
    size: int                                     # bytes charged to the index
    worker: str | None = None
    claimed_at: datetime | None = None


@dataclass
class _Flow:
    """One tenant's candidates for one dispatch kind: FIFO plus its WFQ tag.

    `events` is kept sorted by arrival sequence, so FIFO holds even for a candidate
    that comes back from a lost worker. `refs` counts pending *and* in-flight entries:
    the flow (and therefore the tenant's fairness state) disappears exactly when the
    tenant has nothing left in the index.
    """

    tag: float
    events: list[str] = field(default_factory=list)
    refs: int = 0


class MemoryScheduler:
    """`ports.Scheduler`: a rebuildable, fair, bounded index.

    Collaborators are injected: `now` is the clock (the database clock's stand-in in
    tests, `lambda: datetime.now(timezone.utc)` in a process), `weights` the per-org
    WFQ weight (default 1.0, the knob a dedicated-capacity SKU would turn), `cost` the
    predicted service time per event.

    Not thread safe and deliberately lock free: every operation is a single
    synchronous critical section with no `await` inside it, so one event can never be
    handed to two workers on one event loop. A threaded host wraps it in a lock.
    """

    def __init__(self, now: Callable[[], datetime], *, limits: PilotSettings = DEFAULTS,
                 weights: Mapping[str, float] | None = None,
                 cost: Callable[[IndexEvent], float] = _one_second,
                 max_items: int = MAX_INDEX_ITEMS,
                 max_bytes: int = MAX_INDEX_BYTES) -> None:
        self._now = now
        self.limits = limits
        self._weights = dict(weights or {})
        for org_id, weight in self._weights.items():
            # A non-positive or non-finite weight is not a slow tenant, it is a
            # division by zero (or a NaN that poisons every comparison) in the middle
            # of dispatch. Refuse it where it is configured.
            if not (weight > 0) or weight == float("inf"):
                raise ValueError(f"weight for {org_id} must be finite and positive: {weight!r}")
        self._cost = cost
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._entries: dict[str, _Entry] = {}     # event_id -> entry (pending + in flight)
        self._flows: dict[tuple[str, str], _Flow] = {}    # (kind, org_id) -> flow
        self.acknowledged: set[str] = set()
        self._bytes = 0
        self._seq = 0
        self._virtual_time = 0.0

    # --- port ---------------------------------------------------------------
    async def enqueue(self, event: IndexEvent) -> bool:
        """Replay safe: the same stable event id indexes exactly one candidate.

        A full index raises `capacity_exhausted` (with `Retry-After`) instead of
        silently dropping the candidate: the outbox drain must be able to tell "not
        indexed, come back" from "already indexed". PostgreSQL still holds the job, so
        the refusal costs throughput, never work.
        """
        if event.event_id in self._entries or event.event_id in self.acknowledged:
            return False
        size = len(compact_bytes(event))
        if len(self._entries) + 1 > self._max_items:
            raise errors.CapacityExhausted(
                f"the scheduling index holds its maximum of {self._max_items} candidates")
        if self._bytes + size > self._max_bytes:
            raise errors.CapacityExhausted(
                f"the scheduling index holds its maximum of {self._max_bytes} queued bytes")
        self._seq += 1
        self._entries[event.event_id] = _Entry(event=event, seq=self._seq, size=size)
        self._bytes += size
        flow = self._flow(event.kind, event.org_id)
        flow.events.append(event.event_id)        # seq increases, so this stays sorted
        flow.refs += 1
        return True

    async def claim_candidate(self, worker_id: str, *,
                              kind: OutboxKind | None = None) -> IndexEvent | None:
        """The next candidate under the fair order, or `None`.

        Work conserving: if any candidate of the requested kind is available and not in
        flight, one is returned. Visibility is a timeout measured from the claim, so a
        lost worker's candidate comes back without touching PostgreSQL, and a
        preparation candidate comes back on the shorter preparation lease (R52).
        """
        if kind is not None and kind not in DISPATCH_KINDS:
            # R55: an unknown kind is a caller bug. Answering `None` reported it as "the
            # index is empty", so a pool with a misspelled kind idled for ever against a
            # full queue and looked healthy doing it.
            raise errors.InvalidRequest(
                f"{kind!r} is not a dispatch kind; the index carries "
                f"{', '.join(k.value for k in DISPATCH_KINDS)}")
        now = self._now()
        self._return_expired(now)
        chosen = self._select(kind, now)
        if chosen is None:
            return None
        flow, event_id = chosen
        entry = self._entries[event_id]
        flow.events.remove(event_id)
        # Start-time fair queuing: the flow starts no earlier than the index's virtual
        # time (so an idle or delayed tenant gets no stored-up credit), that start
        # becomes the virtual time, and the tag advances by the service this dispatch
        # hands out (so a backlogged tenant cannot be overtaken for ever either).
        start = max(flow.tag, self._virtual_time)
        cost = self._cost(entry.event)
        if not (cost > 0) or cost == float("inf"):
            raise ValueError(f"service cost must be finite and positive: {cost!r}")
        self._virtual_time = start
        flow.tag = start + cost / self._weight(entry.event.org_id)
        entry.worker, entry.claimed_at = worker_id, now
        return entry.event

    async def acknowledge(self, event: IndexEvent) -> None:
        """Done with it. The candidate is gone for good; the job's truth is in
        PostgreSQL, so the acknowledgment erases no durable fact."""
        self._forget(event.event_id)
        self.acknowledged.add(event.event_id)

    async def remove(self, job_id: str) -> None:
        """Cancellation: drop every candidate of this job, pending or in flight.

        Deliberately *not* remembered as acknowledged. A replayed dispatch event for a
        cancelled job may be re-indexed, and `JobStore.claim` refuses it; remembering
        it instead would make the index authoritative about cancellation, which is
        exactly the mistake this port exists to avoid.
        """
        for event_id, entry in list(self._entries.items()):
            if entry.event.job_id == job_id:
                self._forget(event_id)

    async def rebuild(self, snapshot: tuple[IndexEvent, ...]) -> int:
        """Rebuild from PostgreSQL truth; returns the number of candidates indexed.

        Semantics this adapter pins (the contracts leave them open, Q1 evidence
        records them):

        * `pending` becomes exactly the snapshot, de-duplicated by event id, in
          snapshot order. The caps are **not** applied: a rebuild is recovery, and
          refusing part of PostgreSQL's truth would lose throughput permanently
          instead of briefly.
        * **in-flight entries are dropped.** A pre-rebuild claim was only a hint; the
          worker holding it holds its right to execute from `JobStore.claim`, whose
          lease and generation fence a second attempt. Keeping them would leave the
          index believing in workers nobody can find.
        * **acknowledged ids are cleared.** Keeping them would let a rebuild silently
          drop a job PostgreSQL still reports as queued (a requeued job whose earlier
          candidate was acknowledged). Replay safety after a rebuild comes from the
          snapshot itself, and duplicate execution is still impossible because the
          index never authorizes execution.
        * **fairness state restarts.** Every flow in the snapshot begins at virtual
          time zero, so no tenant inherits a penalty or a credit from an index that no
          longer exists, and no tenant that is absent from the snapshot keeps state.
        """
        self._entries.clear()
        self._flows.clear()
        self.acknowledged.clear()
        self._bytes = 0
        self._seq = 0
        self._virtual_time = 0.0
        for event in snapshot:
            if event.event_id in self._entries:
                continue
            self._seq += 1
            size = len(compact_bytes(event))
            self._entries[event.event_id] = _Entry(event=event, seq=self._seq, size=size)
            self._bytes += size
            flow = self._flow(event.kind, event.org_id)
            flow.events.append(event.event_id)
            flow.refs += 1
        return len(self._entries)

    # --- observability ------------------------------------------------------
    def depth(self) -> int:
        """Pending plus in flight, as the fake reports it."""
        return len(self._entries)

    def stats(self) -> dict[str, object]:
        """Index depth, bytes and waiting age.

        Depth here is **index depth**, never admission capacity: a job is admitted by
        PostgreSQL and may be queued while this index is empty (after a loss) or
        indexed while it is full.
        """
        now = self._now()
        pending = [entry for entry in self._entries.values() if entry.claimed_at is None]
        waiting = [entry for entry in pending if entry.event.available_at <= now]
        depth_by_kind: dict[str, int] = {kind.value: 0 for kind in DISPATCH_KINDS}
        for entry in self._entries.values():
            depth_by_kind[entry.event.kind.value] += 1
        return {
            "items": len(self._entries),
            "bytes": self._bytes,
            "pending": len(pending),
            "inflight": len(self._entries) - len(pending),
            "flows": len(self._flows),
            "acknowledged": len(self.acknowledged),
            "depth_by_kind": depth_by_kind,
            "oldest_wait_s": max(((now - entry.event.available_at).total_seconds()
                                  for entry in waiting), default=0.0),
        }

    def tags(self) -> dict[tuple[str, str], float]:
        """The live fairness state, for tests and for Q2's differential run."""
        return {key: flow.tag for key, flow in self._flows.items()}

    # --- internals ----------------------------------------------------------
    def _weight(self, org_id: str) -> float:
        return self._weights.get(org_id, 1.0)

    def _flow(self, kind: OutboxKind, org_id: str) -> _Flow:
        """The tenant's flow for this kind, created at the current virtual time.

        The textbook start-time fair queuing arrival rule: an arriving flow's tag is the
        system's virtual time, so a tenant that was absent starts level with the others
        rather than with a credit for every dispatch it missed. It is *provably
        redundant* with the clamp in `claim_candidate` (an unserved flow's tag can only
        be below the virtual time, and dispatch clamps it back up; a later-created flow
        can never hold an older arrival sequence, so the fair order comes out the same
        either way). Kept because it is the rule the algorithm is named after and the
        one thing that still holds the line if the clamp is ever edited; Q1's evidence
        records that it carries no mutant of its own for exactly that reason.
        """
        key = (kind.value, org_id)
        flow = self._flows.get(key)
        if flow is None:
            flow = self._flows[key] = _Flow(tag=self._virtual_time)
        return flow

    def _visibility_s(self, event: IndexEvent) -> float:
        """R52: a preparation candidate comes back on the shorter preparation lease,
        so a lost transcode worker is noticed early enough for a bounded retry."""
        return (self.limits.preparation_lease_ttl_s if event.is_preparation
                else self.limits.lease_ttl_s)

    def _return_expired(self, now: datetime) -> None:
        touched: set[tuple[str, str]] = set()
        for event_id, entry in self._entries.items():
            if entry.claimed_at is None:
                continue
            if now >= entry.claimed_at + timedelta(seconds=self._visibility_s(entry.event)):
                entry.worker = entry.claimed_at = None
                flow = self._flow(entry.event.kind, entry.event.org_id)
                flow.events.append(event_id)
                touched.add((entry.event.kind.value, entry.event.org_id))
        for key in touched:
            # Back in arrival order: a returned candidate is older than everything
            # queued behind it, so appending and re-sorting keeps FIFO exact.
            self._flows[key].events.sort(key=lambda event_id: self._entries[event_id].seq)

    def _select(self, kind: OutboxKind | None, now: datetime) -> tuple[_Flow, str] | None:
        """The fair choice: smallest `(tag, arrival sequence)` over flows with an
        available candidate. A total order, so no dict or set iteration order can
        change the answer."""
        best: tuple[tuple[float, int], _Flow, str] | None = None
        for (flow_kind, _org_id), flow in self._flows.items():
            if kind is not None and flow_kind != kind:
                continue
            candidate = next((event_id for event_id in flow.events
                              # ponytail: O(pending) per flow per claim, bounded by
                              # MAX_INDEX_ITEMS; a per-flow "next available" heap is
                              # the upgrade if a claim ever shows up in a profile.
                              if self._entries[event_id].event.available_at <= now), None)
            if candidate is None:
                continue
            order = (flow.tag, self._entries[candidate].seq)
            if best is None or order < best[0]:
                best = (order, flow, candidate)
        return None if best is None else (best[1], best[2])

    def _forget(self, event_id: str) -> None:
        """Drop one entry and, with it, any fairness state it was the last reason for."""
        entry = self._entries.pop(event_id, None)
        if entry is None:
            return
        self._bytes -= entry.size
        key = (entry.event.kind.value, entry.event.org_id)
        flow = self._flows.get(key)
        if flow is None:
            return
        if event_id in flow.events:
            flow.events.remove(event_id)
        flow.refs -= 1
        if flow.refs <= 0:
            # No stale fairness state for an empty or cancelled tenant: the flow is
            # gone, and if the tenant comes back it starts at the current virtual time.
            del self._flows[key]
