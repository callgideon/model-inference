"""The eight service boundaries, one Protocol per row of the contracts-v1 ports
table, with that table's operation names.

Protocols, not base classes: an adapter satisfies one by shape, so D's psycopg
JobStore and the in-memory fake in `fakes/` are interchangeable without importing
each other. Every operation is `async`, takes and returns records, and raises
`DomainError` subclasses. Clock, ID and storage collaborators arrive through the
adapter's constructor, so no operation reads the wall clock or the environment.

No port exposes SQL, another tenant's rows or a raw object path: `*_owned`
operations take the caller's org and raise `NotFound` for anything else.

The ninth row of the table, console services, is TypeScript
(`apps/app/lib/contracts/services.ts`, 08 §9) and has no Python protocol.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from .records import (Admission, AuthContext, Chunk, ConsentSnapshot, Cursor, EngineEvent,
                      Feedback, IdempotencyRef, IndexEvent, JudgeRun, Lease, MediaRef,
                      NormalizedRequest, PreparedRequest, TerminalOutcome, TraceEnvelope,
                      TraceOfferResult)


@runtime_checkable
class JobStore(Protocol):
    """D. Durable authority: acceptance, fencing, terminal settlement."""

    async def admit(self, request: NormalizedRequest, idem: IdempotencyRef,
                    caps: tuple[Any, ...], hold: Decimal) -> Admission:
        """One transaction: recheck authorization, capacity and balance, reserve
        preparation/inference/journal capacity and the maximum hold, insert the
        `preparing` job and its dispatch outbox. An idempotent replay returns the
        original admission with `replayed=True`; a changed payload raises
        `IdempotencyConflict`; an expired mapping raises `IdempotencyExpired`
        rather than silently admitting a second billable job."""

    async def get_owned(self, org_id: str, job_handle: str) -> tuple[Admission, TerminalOutcome | None]:
        """Ownership-checked lookup. Possession of a handle is never enough."""

    async def prepared(self, job_handle: str, media: tuple[MediaRef, ...]) -> Admission:
        """Store immutable prepared refs, then atomically `preparing -> queued`
        with the inference dispatch outbox; releases preparation capacity."""

    async def claim(self, job_id: str, worker_id: str) -> Lease:
        """`queued -> running`, generation incremented from the database clock."""

    async def heartbeat(self, lease: Lease) -> Lease:
        """Renew a lease fenced on generation, owner, state and expiry."""

    async def cancel(self, org_id: str, job_handle: str) -> TerminalOutcome:
        """Durable cancellation of any nonterminal state, serialized against
        completion; if completion won, the committed outcome is returned."""

    async def complete(self, lease: Lease, outcome: TerminalOutcome) -> TerminalOutcome:
        """The single terminal settlement: outcome, authoritative usage, ledger,
        capacity release, terminal journal event and projection outboxes in one
        transaction. Repeating the identical completion is idempotent; a
        different one raises `AlreadyTerminal`."""

    async def recover(self, now: datetime) -> tuple[TerminalOutcome | IndexEvent, ...]:
        """Requeue only prepublication attempts within retry and absolute deadline
        limits, terminalize the rest, and release aged unknown-usage holds."""


@runtime_checkable
class StreamStore(Protocol):
    """D. The PostgreSQL output journal: commit before relay, always."""

    async def append(self, lease: Lease, events: tuple[EngineEvent, ...]) -> tuple[Chunk, ...]:
        """Commit a batch, then return the committed chunks for relay. The first
        append establishes the publication marker that forbids regeneration."""

    async def read_owned(self, org_id: str, job_handle: str, cursor: Cursor | None,
                         limit: int) -> tuple[tuple[Chunk, ...], Cursor | None]:
        """Bounded ownership-checked replay. A pruned cursor raises `ReplayGap`,
        an expired journal `JournalExpired`; output is never regenerated."""

    async def finalize_in_transaction(self, outcome: TerminalOutcome) -> Chunk:
        """The terminal journal event, written in the settling transaction."""

    async def expire(self, now: datetime) -> int:
        """Prune chunks past their TTL and free their bytes; returns the count."""


@runtime_checkable
class MediaStore(Protocol):
    """M. Immutable tenant-scoped content; callers never choose a path."""

    async def stage(self, org_id: str, request: NormalizedRequest) -> tuple[MediaRef, ...]:
        """Durably stage the canonical payload and inline media before acceptance."""

    async def prepare(self, job_handle: str, profile: str) -> tuple[MediaRef, ...]:
        """Produce immutable prepared refs for a profile version."""

    async def create_upload(self, org_id: str, constraints: dict[str, Any]) -> dict[str, Any]:
        """Issue an owned upload handle and a constrained destination reference."""

    async def finalize_upload(self, org_id: str, upload_handle: str) -> MediaRef:
        """Verify ownership, size and checksum of the uploaded object."""

    async def resolve_owned(self, org_id: str, ref: str) -> MediaRef:
        """Resolve an owned handle. Arbitrary storage keys are never accepted."""


@runtime_checkable
class Scheduler(Protocol):
    """Q. A rebuildable index. Membership alone never authorizes execution."""

    async def enqueue(self, event: IndexEvent) -> bool:
        """Replay-safe: the same event id twice indexes one candidate."""

    async def claim_candidate(self, worker_id: str) -> IndexEvent | None:
        """A candidate to try; the winner is decided by `JobStore.claim`."""

    async def acknowledge(self, event: IndexEvent) -> None: ...

    async def remove(self, job_id: str) -> None: ...

    async def rebuild(self, snapshot: tuple[IndexEvent, ...]) -> int:
        """Rebuild from PostgreSQL truth without duplicating or losing jobs."""


@runtime_checkable
class Engine(Protocol):
    """W. Canonical events plus authoritative usage, fenced by the lease."""

    def generate(self, lease: Lease, prepared: PreparedRequest) -> AsyncIterator[EngineEvent]:
        """Async iterator of canonical events; the usage event carries the
        authoritative counts. Not `async def`: it returns the iterator."""

    async def cancel(self, lease: Lease) -> bool: ...

    async def health(self) -> dict[str, Any]: ...

    async def drain(self) -> None: ...


@runtime_checkable
class TraceSink(Protocol):
    """T. Bounded, nonblocking, and never an acceptance dependency."""

    async def offer(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """Accept into memory or drop with a counted reason. Never blocks the
        request path and never promises fsync."""

    async def stats(self) -> dict[str, Any]:
        """In-memory, appended and fsynced counts plus loss reasons, separately."""

    async def flush(self, deadline: datetime) -> dict[str, Any]: ...


@runtime_checkable
class FeedbackService(Protocol):
    """D, adapted by C/G. Ownership comes from durable job identity."""

    async def accept(self, auth: AuthContext, request_id: str, feedback: dict[str, Any],
                     idem: IdempotencyRef) -> Feedback:
        """PostgreSQL commit plus outbox before acknowledgment. Channel and author
        role are server-set; calibration membership needs operator authorization."""

    async def list_owned(self, auth: AuthContext, request_id: str) -> tuple[Feedback, ...]: ...


@runtime_checkable
class JudgeCoordinator(Protocol):
    """D. Hard budget, one submission intent, no ambiguous resubmission."""

    async def reserve(self, run: JudgeRun, consent: ConsentSnapshot, max_cost: Decimal) -> JudgeRun:
        """Transactional worst-case reservation; outstanding and ambiguous runs
        count against the budget, and live submission needs current consent."""

    async def begin_submit(self, run_id: str) -> JudgeRun:
        """Persist one unique submission intent; repeating returns the same one."""

    async def record_submission(self, run_id: str, external_id: str) -> JudgeRun: ...

    async def settle(self, run_id: str, actual: Decimal) -> JudgeRun: ...

    async def quarantine(self, run_id: str, reason: str) -> JudgeRun:
        """Hold the reservation and stop automatic retries."""
