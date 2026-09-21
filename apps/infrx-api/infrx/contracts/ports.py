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
                      TerminalOutcome, TraceEnvelope, TraceLossReason, TraceMode,
                      TraceOfferResult)


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
class TraceCapture(Protocol):
    """T, r1 R27/R37: one request's content, accumulating under the shared budget.

    `add` is deliberately *not* async: it runs on the request path, where it may not
    await anything, and it is O(1) with no disk access. **No operation here raises into
    the request path**: a malformed part, a finished capture or an envelope belonging to
    another request is dropped and counted, never thrown.

    The object is a context manager so G can hold it in a `with`/`finally` and know that
    an abandoned request releases its bytes.
    """

    def add(self, part: bytes | str) -> bool:
        """Charge `part` to the capture budget. False once the budget is breached, at
        which point the whole of this capture's content is discarded and the loss
        counted: a partial capture must never look complete. A part that is not bytes or
        text is also False (dropped, counted `malformed`), never a `TypeError`."""

    async def finish(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """Hand the completed capture to the bounded queue. A capture that lost its
        content finishes as honest metadata with its loss reason.

        Idempotent: calling it again returns the **first** result and queues nothing
        more, and a capture contributes at most one loss count however it ends.

        An **off-mode capture is silent** (R42): `open`/`add`/`finish`/`abandon` store
        nothing and count neither a loss nor a drop, because `02` keeps off-mode jobs out
        of the loss and coverage figures - a sink that counted one per off-mode request
        would report a 100% loss rate for the customers who asked for no tracing. `finish`
        answers `dropped`, meaning "nothing stored", without moving the `dropped` counter.
        Only an `offer` of an off-mode envelope, which has no capture to speak for it, is a
        caller bug and is counted `malformed`.

        A capture contributes **at most one** loss count however it ends and whatever is
        called on it afterwards; counting is idempotent per capture, so `abandon` in a
        `finally` followed by a late `finish` counts once and queues nothing.

        **The capture decides, never the envelope** - otherwise the trace mode would be
        a client-supplied field (R12). The envelope must name this capture's request and
        organization, and its `mode` must be the mode the capture was opened with;
        anything else is dropped and counted `malformed`, with the charge released. A
        capture opened `off` stores nothing whatever arrives. One opened `minimal` stores
        a metadata-only envelope and refuses any content, however the envelope is
        labelled. One opened `full` that never accumulated - no deadline, or its content
        discarded - finishes as honest **metadata**: content stripped, `content_complete`
        false, and exactly one counted loss (`abandoned` for an unrecordable capture,
        `memory_budget` for a discarded one); never `loss_reason: none`. So G opens, adds,
        finishes and needs no branch on the mode, and a `minimal` request still produces
        exactly the metadata row 01 requires."""

    async def abandon(self, reason: TraceLossReason) -> None:
        """Release this capture's bytes without queueing anything. Idempotent."""

    def __enter__(self) -> "TraceCapture":
        """r1 R37: `with sink.open(...) as capture:` - the exit abandons an unfinished
        capture, so a request that dies mid-stream cannot leak capture budget."""

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        """Abandons unless `finish` already ran; never swallows the caller's
        exception (returns False)."""


@runtime_checkable
class TraceSink(Protocol):
    """T. Bounded, nonblocking, and never an acceptance dependency."""

    def open(self, request_id: str, org_id: str, mode: TraceMode,
             deadline_at: datetime) -> TraceCapture:
        """r1 R27/R37: begin accumulating content for this request.

        `deadline_at` is the job's absolute deadline and is **required**: it is what
        lets `reap` release the bytes of a capture whose request died without closing
        it, which is the leak R37 exists to close. A capture with no deadline could
        hold budget until the process restarted, so an adapter that is handed `None`
        returns a no-op capture rather than one it can never reap.

        `off` and `minimal` requests - and a `full` request handed no deadline - return a
        **no-op capture**: `add` is False, and `finish` stores what that capture's mode
        allows (a metadata row for `minimal`, nothing for `off`, stripped metadata with
        one counted loss for the unrecordable `full` case). Callers therefore never branch
        on the mode, and nothing here raises."""

    async def offer(self, envelope: TraceEnvelope) -> TraceOfferResult:
        """A metadata-only envelope: accepted into memory or dropped with a counted
        reason. Never blocks the request path, never promises fsync and **never
        raises into it** - an off-mode envelope is dropped and counted `malformed`."""

    async def stats(self) -> dict[str, Any]:
        """In-memory, appended and fsynced counts plus loss reasons, separately."""

    async def flush(self, deadline: datetime) -> dict[str, Any]: ...

    def reap(self, grace_s: float) -> int:
        """r1 R37: release the bytes of captures still open past their job's
        `deadline_at` plus `grace_s`, counting each under `TraceLossReason.abandoned`.
        Returns how many were reaped; idempotent, and a negative grace is clamped to
        zero rather than reaping live captures. A real sink runs this on a timer."""


@runtime_checkable
class FeedbackService(Protocol):
    """D, adapted by C/G. Ownership comes from durable job identity."""

    async def accept(self, auth: AuthContext, request_id: str, feedback: dict[str, Any],
                     idem: IdempotencyRef) -> Feedback:
        """PostgreSQL commit plus outbox before acknowledgment. Channel and author
        role are server-set; calibration membership needs operator authorization.

        R3: the body is one signal (`name`, `value`, optional `comment`); an empty
        body is `invalid_request` and `idem.key` is required, so every submission is
        replay-safe. `idem` must name the caller's organization (R10). R31: the author
        role is always `customer` here, whatever the session - a client may not send
        provenance at all, and `calibration_set` is refused."""

    async def label_calibration(self, auth: AuthContext, request_id: str, label: str,
                                idem: IdempotencyRef) -> Feedback:
        """R31/R19: the only path to `author_role=operator` with calibration
        membership. Operator only and platform-wide (R26): the tenant comes from the
        labelled row, not from the operator's session. Idempotent and audited."""

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
        """Hold the reservation, stop automatic retries and leave the run
        `ambiguous`, i.e. resolvable through `resolve_ambiguous` whether or not a
        provider id is known: a quarantine that held budget with no way out would be
        a leak. A closed run (`settled`, `cancelled`, `quarantined`) is not reopened."""

    async def resolve_ambiguous(self, run_id: str, operator: AuthContext,
                                resolution: JudgeResolution, reason: str, *,
                                external_id: str | None = None) -> JudgeRun:
        """R8, operator only: the one way out of `ambiguous`.

        `adopt_provider_evidence` requires the discovered provider id and continues
        to collection; `release_reservation` is terminal `quarantined` with the
        reservation released and **refuses** an `external_id` (R23). Either way an
        append-only audit record is written and no second submission is created."""
