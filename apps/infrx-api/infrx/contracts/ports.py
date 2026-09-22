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

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from .records import (Admission, AuthContext, AuthorRole, Chunk, ConsentSnapshot, ContentState,
                      Cursor, EngineEvent,
                      Feedback, IdempotencyRef, IndexEvent, JudgeResolution, JudgeRun, Lease,
                      OutboxKind,
                      MediaRef, NormalizedRequest, PreparedRequest, ReservationKind,
                      TerminalOutcome, TraceEnvelope, TraceLossReason, TraceMode,
                      TraceOfferResult, Work)


@runtime_checkable
class JobStore(Protocol):
    """D. Durable authority: acceptance, fencing, terminal settlement."""

    async def admit(self, request: NormalizedRequest, idem: IdempotencyRef,
                    caps: tuple[ReservationKind, ...] = ()) -> Admission:
        """One transaction: recheck authorization, capacity and balance, reserve
        preparation/inference/journal capacity and the maximum hold, insert the
        `preparing` job and its dispatch outbox. An idempotent replay returns the
        original admission with `replayed=True`; a changed payload raises
        `IdempotencyConflict`; an expired mapping raises `IdempotencyExpired`
        rather than silently admitting a second billable job.

        `caps` names *extra* reservation kinds beyond the three every admission
        takes; amounts come from the store's own limits, never from the caller. The
        request UUID is the job key, so re-admitting one raises `StateConflict` (R6).

        **r1 R53: no caller-supplied hold.** Since R45 only the store knows the rates,
        the store takes the price snapshot and computes `Admission.maximum_hold` from
        it and the request's validated token ceilings in the **same transaction**,
        rounding up (§4). A hold a caller computed is a number from before the price it
        is meant to cover: a rate moving between gateway validation and admission left
        a job admitted at the new price holding for the old one, and a valid
        in-envelope completion then settled `platform_error` with a zero debit.
        `Admission.maximum_hold` is the store's answer, never an echo of the caller's.

        The transaction rechecks key revocation, org suspension *and* current
        entitlement (`ModelNotEntitled`), reserves a `preparation` unit against
        `MAX_PREPARING_JOBS` (R1) and captures the deadline `budgets` on the
        admission (R4). `idem` must name the request's own organization (R10).

        R29 (as amended in 08 §10 by the R-3 audit correction): a `deadline_at` at or
        before the store's `db_now` is refused (`invalid_request`, no side effects);
        otherwise the store keeps `min(caller_deadline, db_now + preparation + queue +
        generation budgets)`, derived from its own clock and budgets snapshot, never the
        gateway's, and pinned for idempotent replays."""

    async def get_owned(self, org_id: str, job_handle: str) -> tuple[Admission, TerminalOutcome | None]:
        """Ownership-checked lookup. Possession of a handle is never enough."""

    async def claim_preparation(self, job_id: str, worker_id: str) -> Lease:
        """r1 R46: `Lease(kind=preparation)` for a `preparing` job, fenced exactly like
        an inference lease (own generation counter, owner, state, expiry).

        At most one live preparation lease per job: a second claim while one is unexpired
        is `not_claimable`, because two preparation workers writing prepared refs for the
        same job is the media equivalent of two workers appending output. `recover`
        reaps an expired one and the job stays preparable, bounded by
        `MAX_PREPUBLICATION_RETRIES` further attempts (so three claims in total) and by
        `preparation_deadline_at`, past which the job is `preparation_failed` (R29)."""

    async def prepared(self, lease: Lease, media: tuple[MediaRef, ...]) -> Admission:
        """Store immutable prepared refs, then atomically `preparing -> queued`
        with the inference dispatch outbox; releases preparation capacity.

        r1 R46: fenced on the **preparation lease** (`02` §3), like every other
        execution mutation: generation, owner, state, expiry and the R29 phase
        deadlines. A superseded preparation worker returning late mutates nothing;
        it used to be addressed by job handle and therefore needed no token at all."""

    async def load_work(self, lease: Lease) -> Work:
        """r1 R46: the only way a lease holder reads what it must execute.

        Fenced like a mutation: a stale, foreign or wrong-kind lease gets a typed
        refusal (`stale_lease`/`not_found`/`already_terminal`) and **no data**. Nothing
        else hands a worker the request, the media refs, the price snapshot or the
        budgets, so losing the fence loses the work."""

    async def claim(self, job_id: str, worker_id: str) -> Lease:
        """`queued -> running`, generation incremented from the database clock.
        Returns `Lease(kind=inference)`."""

    async def heartbeat(self, lease: Lease) -> Lease:
        """Renew an **inference** lease fenced on generation, owner, state and expiry.

        A preparation lease is refused (`invalid_request`): the preparation budget and
        the lease TTL are both bounded and equal by default, so there is no renewal to
        make, and a silent no-op would let a preparation worker believe it still held a
        fence it had lost."""

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
    """M. Immutable tenant-scoped content; callers never choose a path.

    r1 R46 fixes the addressing: **internal** operations take `job_id` (the request
    UUID), **tenant-facing** ones take the caller's org plus an opaque handle. So
    `attach`/`prepare` here, `claim_preparation`/`claim`/`load_work` and `IndexEvent`
    on the JobStore all say `job_id`, while `get_owned`/`cancel`/`read_owned` and
    `resolve_owned` say `(org_id, handle)`. A handle is a customer-facing lookup key
    that must be ownership-checked on sight; a job id is the internal identity every
    durable row is keyed by, and no internal caller should have to resolve one to the
    other (or be tempted to skip the ownership check when it does).
    """

    async def stage(self, org_id: str, request: NormalizedRequest) -> tuple[MediaRef, ...]:
        """Durably stage the canonical payload before acceptance, with refs **this store
        produced** (F2R item 4, R66).

        Every url/inline ref must be one the store materialized for `org_id` (M's
        `materialize`; the conformance harness's `materialized(org_id, ref)` hook), and
        every upload ref a finalized upload it resolves; anything else - never
        materialized, another tenant's, or a real handle claiming other content - is
        `not_found`, and nothing is written. The refs returned are the store's own
        records (key, size, type, duration), never the request's copies. The request's
        messages carry one `video_url` part per ref, in order (R58).

        `MediaRef.duration_s` is written by **preparation** (M2's probe, before the
        object is stored); a caller never supplies it, and a store with no decoder
        records `None`."""

    async def attach(self, job_id: str, refs: tuple[MediaRef, ...]) -> None:
        """r1 R46: bind staged refs to an admitted job, as the job row does in
        PostgreSQL. A real port operation rather than a test-only hook, because
        `prepare` cannot work without it and M's adapter has to implement it.

        r1 R52: every ref must belong to the **job's** organization, or `not_found`. A
        foreign ref used to attach and be caught two phases later by `prepared`, after
        `prepare` had transcoded it into this tenant's prefix.

        r1 R55: that organization is read from the **job row**. With an `org_id` argument
        `attach(jobA, ORG_B, (refB,))` satisfied its own check, because the caller named
        the tenant its refs belonged to - and 06 is explicit that server-derived identity
        is never taken from an untrusted argument. A refused attach stores nothing."""

    async def prepare(self, job_id: str, profile: str) -> tuple[MediaRef, ...]:
        """Produce immutable prepared refs for a profile version."""

    async def create_upload(self, org_id: str, constraints: dict[str, Any]) -> dict[str, Any]:
        """Issue an owned upload handle and a constrained destination reference."""

    async def finalize_upload(self, org_id: str, upload_handle: str) -> MediaRef:
        """Verify ownership, size and checksum of the uploaded object."""

    async def resolve_owned(self, org_id: str, ref: str) -> MediaRef:
        """Resolve an owned handle. Arbitrary storage keys are never accepted."""


@runtime_checkable
class MediaPreparation(Protocol):
    """M2's admission-time preparation, a separate protocol from `MediaStore` (whose
    `prepare(job_id, profile)` keeps its signature): G calls this **before** `stage`.

    Implemented by `infrx.media.prepare.MediaPreparation` (M2)."""

    async def prepare_request(self, org_id: str, request: NormalizedRequest) -> NormalizedRequest:
        """The validated request with its media materialized, measured and referenced:
        `media` holds one store-produced ref per video part, in order, each carrying the
        measured `duration_s`, and `messages` name those refs instead of the customer's
        URLs, so `stage` accepts exactly this record (F2R item 4). All or nothing: a
        source that cannot be fetched, read or served refuses the whole request.
        `payload_digest` is not recomputed - it names the customer's body for
        idempotency. `request.org_id` must be `org_id`, or `forbidden`."""


@runtime_checkable
class Scheduler(Protocol):
    """Q. A rebuildable index. Membership alone never authorizes execution."""

    async def enqueue(self, event: IndexEvent) -> bool:
        """Replay-safe: the same event id twice indexes one candidate."""

    async def claim_candidate(self, worker_id: str, *,
                              kind: OutboxKind | None = None) -> IndexEvent | None:
        """A candidate to try; the winner is decided by `JobStore.claim`.

        r1 R52: `kind` selects `prepare_dispatch` or `inference_dispatch`, so a
        preparation worker can be fed from the index rather than from a side channel.
        `None` means "anything". A candidate of the wrong kind is not a refusal a pool
        should have to discover through `claim`.

        r1 R55: an **unknown** kind is `invalid_request`, not `None`. Answering "no
        candidate" for a misspelled kind reports a caller bug as an empty index, so a pool
        idles for ever against a full queue and looks healthy while it does it."""

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
        """In-memory, appended and fsynced counts plus loss reasons, separately.

        The exported suites read these keys, so an adapter that omits one skips an
        assertion silently. They are the contract:

        | Key | Meaning |
        |---|---|
        | `accepted` | records taken into memory (`offer`/`finish` answered `accepted_in_memory`) |
        | `dropped` | records refused, each with a counted `loss_reasons` entry |
        | `in_memory` | records held in memory, not yet appended |
        | `in_memory_content_bytes` | content bytes currently charged to the process budget |
        | `in_memory_metadata_bytes` | metadata bytes currently charged: per record `max(declared metadata_bytes, len(serialized envelope))` (R65) |
        | `open_captures` | captures opened and not yet finished or abandoned |
        | `appended` | records written to the spool but not necessarily fsynced |
        | `fsynced` | records fsynced, i.e. the only ones durability is claimed for |
        | `loss_reasons` | `{reason_value: count}`, **non-zero counts only** - a reason with
          nothing behind it is not a loss, and reporting `shutdown: 0` made a silent
          off-mode process look lossy (R42) |

        02 requires the three durability states separately, so a caller can never read
        "appended" as "safe".
        """

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
        provenance at all, and `calibration_set` is refused.

        R43: `name=calibration_label` is refused as input (it is a stored-entry name
        only), text and comment are bounded at 4000 characters, and a **suspended**
        organization is `org_suspended` (R33): suspension gates new work and
        configuration changes, so a submission is refused while every read still
        works."""

    async def label_calibration(self, auth: AuthContext, request_id: str, label: str,
                                rubric_version: int, idem: IdempotencyRef, *,
                                comment: str | None = None) -> Feedback:
        """R31/R19/R43: the only path to `author_role=operator` with calibration
        membership. Operator only and platform-wide (R26): the tenant comes from the
        labelled row, not from the operator's session. Idempotent and audited.

        `label` is one of `records.CalibrationLabel`; `rubric_version` is a required
        integer in 1..1000 (R43: an integer everywhere, as `research/traces/04` stores
        it). The stored row is an ordinary `Feedback` with `name=calibration_label`,
        `calibration_set=True` and that rubric version - there is no second shape."""

    async def list_owned(self, auth: AuthContext, request_id: str) -> tuple[Feedback, ...]:
        """The viewer's own feedback for one request.

        R35/R41 bind the Python half too: for a **non-operator** `AuthContext` this
        excludes calibration labels entirely and reports an operator-authored
        principal as the literal `platform` (`wire.PLATFORM_ACTOR`); an operator
        `AuthContext` sees the rows as stored. `wire.FeedbackList.for_viewer` applies
        the same projection to a body built from any other source."""

    async def list_calibration(self, auth: AuthContext, request_id: str) -> tuple[Feedback, ...]:
        """R35, mirroring the console's `calibration.list`: operator-only, the one view
        that shows calibration labels and the operator principals that authored them.
        A non-operator caller is `forbidden`, not an empty list, and the labels are
        platform-wide (R26) because a calibration set spans tenants."""


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


# ==========================================================================
# J's read side and price source (F2R item 5: moved from `infrx/judge`)
# ==========================================================================
#: `research/traces/06` §3.1: a truncated answer is the failure stratum's first member.
FINISH_REASON_LENGTH = "length"
#: R56: a 5xx produced no answer to grade.
NO_OUTPUT_STATUS = 500


@dataclass(frozen=True)
class TraceCandidate:
    """One candidate trace, as the source reports it: **raw facts only**.

    R56: the sampler derives the strata from `finish_reason`, `http_status` and
    `schema_valid` rather than trusting a `failure` flag the query computed, so the
    policy lives in one place and a query change cannot quietly redefine it.
    `feedback` carries the trace's feedback *rows* rather than two booleans, so the
    R43 distinction between a customer signal and an operator label is decided from
    the persisted shape - and only from rows that actually belong to this trace.
    """

    request_id: str
    org_id: str
    trace_mode: TraceMode
    content_state: ContentState
    started_at: datetime
    model_revision: str
    http_status: int = 200
    finish_reason: str | None = None
    schema_valid: bool = True
    media_available: bool = False
    feedback: tuple[Feedback, ...] = ()

    @property
    def own_feedback(self) -> tuple[Feedback, ...]:
        """R56: a row counts only when its organization **and** request match.

        Without both checks an org-B label attached to an org-A candidate excluded that
        candidate as already labelled, and an org-B thumb for another request moved an
        org-A trace into the customer-feedback stratum - a cross-tenant fact deciding a
        tenant's calibration set.
        """
        return tuple(entry for entry in self.feedback
                     if entry.org_id == self.org_id and entry.request_id == self.request_id)

    @property
    def calibration_labels(self) -> tuple[Feedback, ...]:
        """r1 R43: membership is the boolean on a `calibration_label` entry. The record
        already refuses any disagreement between name, flag and rubric version, so this
        one field is the whole test - and `by_operator` is *not* it (R56): the platform
        typing an ordinary comment is not an operator verdict against a rubric."""
        return tuple(entry for entry in self.own_feedback if entry.calibration_set)

    @property
    def has_customer_feedback(self) -> bool:
        """The `feedback` stratum: a *customer* told us something about this answer.
        A judge's own score is not customer feedback, a label is not, and neither is an
        ordinary entry the platform made on the customer's behalf (R56/R50)."""
        return any(entry.author_role is AuthorRole.customer and not entry.calibration_set
                   and not entry.by_operator for entry in self.own_feedback)

    @property
    def failed(self) -> bool:
        """R56 / `06` §3.1: the failure stratum is a truncated answer or an invalid
        structured output. Derived here, never taken from the source."""
        return self.finish_reason == FINISH_REASON_LENGTH or self.schema_valid is False

    @property
    def produced_no_output(self) -> bool:
        return self.http_status >= NO_OUTPUT_STATUS

    def labelled_against(self, rubric_version: int) -> bool:
        return any(entry.rubric_version == rubric_version for entry in self.calibration_labels)


class CandidateSource(Protocol):
    """J's read side over T's trace projection plus D's feedback rows. Injected.

    Contract for an adapter (moved from `judge/sampling.py` by F2R item 5; the shape
    stays minimal and tenant-scoped):

    * answer a **synchronous iterable** (a tuple, a list, a generator - not an async
      generator and not a coroutine), of rows for **`org_id` only**, `trace_mode = full`,
      started at or after `since`, at most `limit` of them;
    * carry `request_id` in the **frozen lower-case UUIDv4 form** every contract record
      uses (`ids.is_request_id`): there is exactly one spelling of an id here, and a row
      carrying any other is excluded as `malformed_row` rather than normalized (R3-B1);
    * return rows **deduplicated by `request_id` and in a stable order** (`06` §3.1 queries
      `FINAL` for exactly this reason). The sampler enforces both anyway - it truncates to
      the bound and deduplicates on the exact id - but only a stable order makes *which*
      rows fall inside the bound reproducible;
    * carry the **raw**, **typed** facts and let the sampler derive the strata:
      `http_status` an `int`, `schema_valid`/`media_available` a `bool`, `started_at` a
      UTC-aware `datetime` no later than the plan's `now`, `trace_mode`/`content_state`
      the enums, and `finish_reason` either `None` or the engine's token **exactly as
      lower-case text** (`"length"`, `"stop"`; the sampler does not fold case, so a
      differently-spelled value is simply not a truncation). A row that breaks any of
      this is excluded on its own as `malformed_row` rather than interpreted;
    * attach only feedback rows belonging to that organization and request.
    """

    async def candidates(self, org_id: str, *, since: datetime,
                         limit: int) -> tuple[TraceCandidate, ...]:
        ...


class RateTable(Protocol):
    """Injected judge price source, mirroring `JobStore`'s `price_for` (r1 R45; moved
    from `judge/cost.py` by F2R item 5).

    `rate_for` answers with the row **effective at `at`** (J's `ProviderRate`), or
    `None`. It raises a `DomainError` only for a caller bug (a naive instant); an
    unpriced model is `None`, never an exception and never a zero.
    """

    def rate_for(self, model: str, at: datetime) -> Any | None:
        ...
