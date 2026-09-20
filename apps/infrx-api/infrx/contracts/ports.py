"""The eight service boundaries, one Protocol per row of the contracts-v1 ports
table, with that table's operation names.

Protocols, not base classes: an adapter satisfies one by shape, so D's psycopg
JobStore and the in-memory fake in `fakes/` are interchangeable without importing
each other. Every operation is `async`, takes and returns records, and raises
`DomainError` subclasses. Clock, ID and storage collaborators arrive through the
adapter's constructor, so no operation reads the wall clock or the environment.

No port exposes SQL, another tenant's rows or a raw object path: `*_owned`
operations take the caller's org and raise `NotFound` for anything else.

Two rules hold for every operation below (contracts v1 revision r1):

* **Tenant coherence (R10).** An operation given two tenant-bearing arguments
  verifies they name the same organization and fails `not_found`/`forbidden`
  otherwise. Mixing them is never a successful cross-tenant read or write.
* **Monetary inputs (R11).** Holds, grants, settlements and judge costs are
  refused when negative, non-finite or outside `numeric(20, 8)`
  (`|value| < 10^12`); only the store's own compensating entries are negative.
  No operation takes a caller-supplied time for an expiry, lease or 24 h
  decision (R7): the adapter reads the database clock inside its transaction.

The ninth row of the table, console services, is TypeScript
(`apps/app/lib/contracts/services.ts`, 08 §9) and has no Python protocol.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from .records import (Admission, AuthContext, Chunk, ConsentSnapshot, Cursor, EngineEvent,
                      Feedback, IdempotencyRef, IndexEvent, JudgeResolution, JudgeRun, Lease,
                      MediaRef, NormalizedRequest, PreparedRequest, ReservationKind,
                      TerminalOutcome, TraceEnvelope, TraceOfferResult)


@runtime_checkable
class JobStore(Protocol):
    """D. Durable authority: acceptance, fencing, terminal settlement."""

    async def admit(self, request: NormalizedRequest, idem: IdempotencyRef,
                    caps: tuple[ReservationKind, ...], hold: Decimal) -> Admission:
        """One transaction: recheck authorization, capacity and balance, reserve
        preparation/inference/journal capacity and the maximum hold, insert the
        `preparing` job and its dispatch outbox. An idempotent replay returns the
        original admission with `replayed=True`; a changed payload raises
        `IdempotencyConflict`; an expired mapping raises `IdempotencyExpired`
        rather than silently admitting a second billable job.

        `caps` names *extra* reservation kinds beyond the three every admission
        takes; amounts come from the store's own limits, never from the caller. The
        request UUID is the job key, so re-admitting one raises `StateConflict`
        (R6), and a negative `hold` raises `InvalidRequest` (R11).

        The transaction rechecks key revocation, org suspension *and* current
        entitlement (`ModelNotEntitled`), reserves a `preparation` unit against
        `MAX_PREPARING_JOBS` (R1) and captures the deadline `budgets` on the
        admission (R4). `idem` must name the request's own organization (R10)."""

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
        different one raises `AlreadyTerminal`. `outcome.job_id` must be the
        lease's job (R10), and a settlement that cannot be built leaves the
        wallet untouched: validation happens before any money moves."""

    async def recover(self) -> tuple[TerminalOutcome | IndexEvent, ...]:
        """Requeue only prepublication attempts within retry and absolute deadline
        limits, terminalize the rest, and release aged unknown-usage holds.

        Takes no caller time (R7): every expiry, lease and 24 h decision reads the
        database clock inside the transaction, so a caller cannot release an
        unknown-usage hold early by claiming it is later than it is."""


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

    async def expire(self, now: datetime | None = None) -> int:
        """Prune chunks past their TTL and free their bytes; returns the count.

        A caller-supplied `now` is a bound at most (R7): the adapter never prunes
        past the database clock, so a future argument cannot expire a live
        journal early."""


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
        role are server-set; calibration membership needs operator authorization.

        R3: the body is one signal (`name`, `value`, optional `comment`); an empty
        body is `invalid_request` and `idem.key` is required, so every submission
        is replay-safe. `idem` must name the caller's organization (R10)."""

    async def list_owned(self, auth: AuthContext, request_id: str) -> tuple[Feedback, ...]: ...


@runtime_checkable
class JudgeCoordinator(Protocol):
    """D. Hard budget, one submission intent, no ambiguous resubmission."""

    async def reserve(self, run: JudgeRun, consent: ConsentSnapshot, max_cost: Decimal) -> JudgeRun:
        """Transactional worst-case reservation; outstanding and ambiguous runs
        count against the budget, and live submission needs current consent.
        `consent` must belong to the run's organization (R10) and `max_cost` is a
        monetary input (R11). Idempotent per `run_id`: a second call returns the
        stored run without resetting its state or intent."""

    async def begin_submit(self, run_id: str) -> JudgeRun:
        """Persist one unique submission intent; repeating returns the same one.

        Consent is rechecked against the **current** consent record, not the
        snapshot taken at `reserve` (R9): a revocation in between blocks egress and
        releases the reservation."""

    async def record_submission(self, run_id: str, external_id: str) -> JudgeRun: ...

    async def settle(self, run_id: str, actual: Decimal) -> JudgeRun: ...

    async def quarantine(self, run_id: str, reason: str) -> JudgeRun:
        """Hold the reservation and stop automatic retries. A terminal run
        (`settled`, `cancelled`) is not reopened."""

    async def resolve_ambiguous(self, run_id: str, operator: AuthContext,
                                resolution: JudgeResolution, reason: str, *,
                                external_id: str | None = None) -> JudgeRun:
        """R8, operator only: the one way out of `ambiguous`.

        `adopt_provider_evidence` requires the discovered provider id and continues
        to collection; `release_reservation` is terminal `quarantined` with the
        reservation released and **refuses** an `external_id` (R23). Either way an
        append-only audit record is written and no second submission is created."""
