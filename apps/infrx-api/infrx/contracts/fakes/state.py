"""In-memory JobStore and StreamStore with the real semantics of
`research/plan/02-durable-protocols.md`.

These are the executable specification, not stubs. They implement idempotent
admission scoped to org+operation+key with payload-hash conflicts and tombstones,
capacity and hold accounting that cannot be double counted, generation fencing on
every mutation, the publication marker that forbids regeneration, exactly one
terminal settlement with authoritative-versus-unknown usage, and the outbox.

One `asyncio.Lock` stands in for PostgreSQL's row locks and the documented global
lock order (capacity scope, org, key, wallet). A real adapter must take those
locks in that order; the fake serializes instead, which is strictly stronger and
keeps concurrency cases deterministic.
# ponytail: one store-wide lock, not per-scope locks. Fine for a fake; D's
# adapter needs the documented order because PostgreSQL will not serialize for it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .. import errors, money
from ..codec import compact_bytes
from ..limits import DEFAULTS, PilotSettings
from ..records import (Admission, BILLABLE_CAUSES, Budgets, CapacityReservation, Chunk,
                       ChunkEventType, Cursor, EngineEvent, HoldState, IdempotencyRef, IndexEvent,
                       JobState, Lease, MediaRef, NormalizedRequest, OutboxEvent, OutboxKind,
                       PriceSnapshot, ReservationKind, SettlementState, TERMINAL_STATES, TerminalCause,
                       TerminalOutcome, Usage, UsageCertainty, states_for_cause)
from .support import FailurePlan, FakeClock, SequentialIds, failure_hooks, money_input

MAX_READ_LIMIT = 1000           # refinement: the bound on one replay page

# released_free = the customer was never going to be charged (rejected, invalid,
# never ran). released_platform_absorbed = we did work and ate the cost.
FREE_CAUSES = frozenset({TerminalCause.invalid_media, TerminalCause.preparation_failed,
                         TerminalCause.queue_wait_expired})


def _phase_deadline(now: datetime, budget_s: float, deadline_at: datetime) -> datetime:
    """r1 R20: a phase instant is the database clock plus that phase's budget,
    clamped by the absolute accepted deadline. No phase outlives the job."""
    return min(now + timedelta(seconds=budget_s), deadline_at)


@dataclass
class _Wallet:
    ledger_total: Decimal = money.ZERO
    reserved_total: Decimal = money.ZERO

    @property
    def available(self) -> Decimal:
        return money.available(self.ledger_total, self.reserved_total)


@dataclass
class _Hold:
    request_id: str
    org_id: str
    amount: Decimal
    state: HoldState = HoldState.held
    reconcile_after: datetime | None = None


@dataclass
class _Idem:
    payload_hash: str
    request_id: str
    expires_at: datetime | None = None       # None while the job is active


@dataclass
class _Job:
    request: NormalizedRequest
    admission: Admission
    state: JobState
    generation: int = 0
    lease: Lease | None = None
    published: bool = False                  # first committed chunk
    attempts: int = 0                        # prepublication requeues used
    prepared: tuple[MediaRef, ...] = ()
    queued_at: datetime | None = None         # start of the current queued spell
    queued_elapsed: timedelta = timedelta(0)  # queue time from earlier spells
    outcome: TerminalOutcome | None = None
    reservations: dict[ReservationKind, CapacityReservation] = field(default_factory=dict)
    # What the winning worker proposed, so its identical retry replays the
    # committed outcome even when the store rewrote the settlement.
    proposal: tuple | None = None

    @property
    def id(self) -> str:
        return self.request.request_id

    @property
    def budgets(self) -> Budgets:
        """r1 R4: the budgets captured at admission, never current configuration."""
        return self.admission.budgets

    @property
    def queue_deadline_at(self) -> datetime | None:
        """r1 R20: set at the first durable `queued` transition, then immutable."""
        return self.admission.queue_deadline_at

    def preparing(self) -> bool:
        reservation = self.reservations.get(ReservationKind.preparation)
        return reservation is not None and reservation.active

    @property
    def terminal(self) -> bool:
        return self.outcome is not None


class _Journal:
    """The global journal byte budget. A job charges its live reservation, or its
    stored unexpired bytes once the reservation is gone: never both."""

    def __init__(self, limits: PilotSettings) -> None:
        self.limits = limits
        self.reserved: dict[str, int] = {}
        self.stored: dict[str, int] = {}

    def charge(self, job_id: str) -> int:
        return max(self.reserved.get(job_id, 0), self.stored.get(job_id, 0))

    def total(self) -> int:
        return sum(self.charge(job_id) for job_id in set(self.reserved) | set(self.stored))

    def reserve(self, job_id: str) -> int:
        want = self.limits.journal_job_reserve_bytes
        if self.total() + want > self.limits.journal_total_bytes:
            raise errors.JournalCapacityExhausted(
                f"journal budget {self.limits.journal_total_bytes} cannot fit another "
                f"{want} byte reservation", retry_after_s=30)
        self.reserved[job_id] = want
        return want

    def store(self, job_id: str, extra: int, *, settling: bool = False) -> None:
        """Per-job and global byte limits, both enforced (02, Output section).

        `settling=True` is the terminal event of the settling transaction: it is
        charged but never refused, because the money has already moved and the
        job's own 16 MiB reservation is still live to cover it.
        """
        stored = self.stored.get(job_id, 0) + extra
        before = self.charge(job_id)
        after = max(self.reserved.get(job_id, 0), stored)
        if not settling:
            if stored > self.limits.journal_job_reserve_bytes:
                raise errors.JournalCapacityExhausted(
                    f"job {job_id} would store {stored} bytes past its per-job reservation "
                    f"of {self.limits.journal_job_reserve_bytes}", retry_after_s=30)
            if self.total() - before + after > self.limits.journal_total_bytes:
                raise errors.JournalCapacityExhausted(
                    f"journal budget cannot fit {extra} more bytes for {job_id}", retry_after_s=30)
        self.stored[job_id] = stored

    def release_reservation(self, job_id: str) -> None:
        """Terminalization frees the unused reservation; stored bytes keep counting
        until they are pruned, or a burst of fast jobs would overrun the disk."""
        self.reserved.pop(job_id, None)

    def prune(self, job_id: str, freed: int) -> None:
        remaining = max(0, self.stored.get(job_id, 0) - freed)
        if remaining:
            self.stored[job_id] = remaining
        else:
            self.stored.pop(job_id, None)


class FakeJobStore:
    """`ports.JobStore`. PostgreSQL is the durable authority; this is it, in RAM."""

    def __init__(self, clock: FakeClock | None = None, ids: SequentialIds | None = None, *,
                 limits: PilotSettings = DEFAULTS, failures: FailurePlan | None = None,
                 journal: _Journal | None = None) -> None:
        self.clock = clock or FakeClock()
        self.ids = ids or SequentialIds()
        self.limits = limits
        self.failures = failure_hooks(failures)
        self.journal = journal or _Journal(limits)
        self.jobs: dict[str, _Job] = {}
        self.by_handle: dict[str, str] = {}
        # The journal half of the same database: settlement writes the terminal
        # event through it, in the transaction that moves the money (02 §7).
        self.stream: FakeStreamStore | None = None
        self.idem: dict[tuple[str, str, str | None], _Idem] = {}
        self.wallets: dict[str, _Wallet] = {}
        self.holds: dict[str, _Hold] = {}
        self.outbox: list[OutboxEvent] = []
        self.revoked_keys: set[str] = set()
        self.suspended_orgs: set[str] = set()
        # r1 R10: the injectable entitlement source. A real adapter replaces the
        # callable with its own query; the fake withdraws (org, model) pairs.
        self.unentitled: set[tuple[str, str]] = set()
        self.is_entitled = lambda org_id, model_revision: (org_id, model_revision) \
            not in self.unentitled
        self._lock = asyncio.Lock()

    # --- test helpers (not part of the port) ---------------------------------
    def grant(self, org_id: str, amount: str | Decimal) -> Decimal:
        """An operator credit grant: positive, append-only in the real ledger.

        r1 R11: a negative grant is refused. Corrections are compensating entries
        the store itself writes, never a caller-supplied negative grant."""
        amount = money_input(amount, "a credit grant")
        wallet = self.wallets.setdefault(org_id, _Wallet())
        wallet.ledger_total = wallet.ledger_total + amount
        return wallet.ledger_total

    def unentitle(self, org_id: str, model_revision: str) -> None:
        self.unentitled.add((org_id, model_revision))

    def entitle(self, org_id: str, model_revision: str) -> None:
        self.unentitled.discard((org_id, model_revision))

    def wallet(self, org_id: str) -> _Wallet:
        return self.wallets.setdefault(org_id, _Wallet())

    def outbox_kinds(self, aggregate_id: str) -> list[OutboxKind]:
        return [event.kind for event in self.outbox if event.aggregate_id == aggregate_id]

    def active_jobs(self, org_id: str | None = None, key_id: str | None = None) -> list[_Job]:
        return [job for job in self.jobs.values()
                if not job.terminal
                and (org_id is None or job.request.org_id == org_id)
                and (key_id is None or job.request.key_id == key_id)]

    # --- port ---------------------------------------------------------------
    async def admit(self, request: NormalizedRequest, idem: IdempotencyRef,
                    caps: tuple[object, ...] = (), hold: Decimal | str = money.ZERO) -> Admission:
        self.failures.before("admit")
        # money.parse allows negatives for ledger deltas; a reservation is never
        # one. A negative hold would inflate `available` (DUR-CAP, r1 R11).
        hold = money_input(hold, "the maximum hold")
        for kind in caps:
            if kind not in tuple(ReservationKind):
                raise errors.InvalidRequest(f"{kind!r} is not a reservation kind")
        if idem.org_id != request.org_id:
            # r1 R10: one organization's idempotency scope never replays, expires or
            # reads another's. Without this, ORG_B replays ORG_A's admission.
            raise errors.Forbidden("the idempotency scope must name the request's org")
        for ref in request.media:
            if ref.org_id != request.org_id:
                raise errors.NotFound("a request may only carry its own org's media")
        async with self._lock:
            now = self.clock.now()
            replay = self._replay(idem, now)
            if replay is not None:
                return replay
            if request.request_id in self.jobs:
                # The request UUID is the job primary key (06, r1 R6): a second
                # admission of the same request would mint a second hold that nothing
                # ever releases. The supported retry is the idempotency key above.
                raise errors.StateConflict(
                    f"request {request.request_id} is already an admitted job")

            # Recheck authorization in the admitting transaction: a cached identity
            # never bypasses revocation, suspension or entitlement.
            if request.key_id in self.revoked_keys:
                raise errors.InvalidApiKey(f"key {request.key_id} is revoked")
            if request.org_id in self.suspended_orgs:
                raise errors.OrgSuspended(f"org {request.org_id} is suspended")
            if not self.is_entitled(request.org_id, request.model_revision):
                raise errors.ModelNotEntitled(
                    f"org {request.org_id} is not entitled to {request.model_revision}")
            self._check_capacity(request)
            self._check_balance(request.org_id, hold)
            # Everything that can refuse the admission runs before anything is
            # reserved: a rejected admission leaves no journal bytes, no hold and
            # no job behind (`_price` fails closed on an unpriced model).
            price = self._price(request)
            self.journal.reserve(request.request_id)
            admission = self._insert(request, idem, hold, now, price)
        self.failures.after_commit("admit")
        return admission

    def _replay(self, idem: IdempotencyRef, now: datetime) -> Admission | None:
        if idem.key is None:
            return None
        record = self.idem.get(idem.scope)
        if record is None:
            return None
        if record.expires_at is not None and now >= record.expires_at:
            # An expired mapping is answered explicitly; it never silently submits
            # a second billable job.
            raise errors.IdempotencyExpired(f"idempotency key expired at {record.expires_at}")
        if record.payload_hash != idem.payload_hash:
            raise errors.IdempotencyConflict("same idempotency key, different canonical payload")
        job = self.jobs[record.request_id]
        return job.admission.model_copy(update={"state": job.state, "replayed": True})

    def _check_capacity(self, request: NormalizedRequest) -> None:
        limits = self.limits
        preparing = len([job for job in self.jobs.values()
                         if not job.terminal and job.preparing()])
        for scope, count, ceiling in (
            ("total", len(self.active_jobs()), limits.max_active_jobs),
            ("org", len(self.active_jobs(org_id=request.org_id)), limits.max_active_jobs_per_org),
            ("key", len(self.active_jobs(key_id=request.key_id)), limits.max_active_jobs_per_key),
            # r1 R1: admission reserves a preparation unit against its own cap.
            # PREPARATION_CONCURRENCY stays the host worker-pool size.
            ("preparation", preparing, limits.max_preparing_jobs),
        ):
            if count >= ceiling:
                raise errors.CapacityExhausted(f"{scope} active job limit {ceiling} reached",
                                               retry_after_s=5)

    def _check_balance(self, org_id: str, hold: Decimal) -> None:
        wallet = self.wallet(org_id)
        if hold > wallet.available:
            raise errors.InsufficientCredit(
                f"maximum hold exceeds available balance for org {org_id}")

    def _insert(self, request: NormalizedRequest, idem: IdempotencyRef, hold: Decimal,
                now: datetime, price: PriceSnapshot) -> Admission:
        handle = self.ids.job_handle()
        reservations = {
            kind: CapacityReservation(request_id=request.request_id, org_id=request.org_id,
                                      key_id=request.key_id, kind=kind, amount=amount,
                                      reserved_at=now)
            for kind, amount in ((ReservationKind.preparation, 1),
                                 (ReservationKind.inference, 1),
                                 (ReservationKind.journal_bytes,
                                  self.limits.journal_job_reserve_bytes))
        }
        budgets = Budgets.of(self.limits, request.execution_mode)
        wallet = self.wallet(request.org_id)
        wallet.reserved_total = wallet.reserved_total + hold
        self.holds[request.request_id] = _Hold(request.request_id, request.org_id, hold)

        event = self._emit(request.request_id, OutboxKind.prepare_dispatch, now,
                           {"job_handle": handle, "request_id": request.request_id})
        admission = Admission(
            request_id=request.request_id, job_handle=handle, org_id=request.org_id,
            key_id=request.key_id, operation=idem.operation, idempotency_key=idem.key,
            payload_hash=idem.payload_hash, price_snapshot=price,
            maximum_hold=hold, reservations=tuple(reservations.values()),
            state=JobState.preparing, outbox=(event,), admitted_at=now,
            deadline_at=request.deadline_at,
            # r1 R4: the accepted job keeps these, whatever configuration does next.
            budgets=budgets,
            # r1 R20: the preparation phase starts here, so its instant is derived
            # here, from the database clock and never beyond the absolute deadline.
            preparation_deadline_at=_phase_deadline(now, budgets.preparation_s,
                                                    request.deadline_at))
        self.jobs[request.request_id] = _Job(request=request, admission=admission,
                                             state=JobState.preparing, reservations=reservations)
        self.by_handle[handle] = request.request_id
        if idem.key is not None:
            self.idem[idem.scope] = _Idem(idem.payload_hash, request.request_id)
        return admission

    def _price(self, request: NormalizedRequest):
        """Admission snapshots the price; an unpriced model fails closed. The fake
        carries the snapshot on the request's parameters for test convenience."""
        snapshot = request.parameters.get("price_snapshot") if request.parameters else None
        if snapshot is None:
            raise errors.InvalidRequest("no price snapshot for the requested model")
        return PriceSnapshot.model_validate(snapshot)

    def _emit(self, aggregate_id: str, kind: OutboxKind, now: datetime,
              payload: dict[str, object]) -> OutboxEvent:
        event = OutboxEvent(event_id=self.ids.event_id(), aggregate_id=aggregate_id, kind=kind,
                            payload=payload, available_at=now)
        self.outbox.append(event)
        return event

    async def get_owned(self, org_id: str, job_handle: str) -> tuple[Admission, TerminalOutcome | None]:
        job = self._owned(org_id, job_handle)
        return job.admission.model_copy(update={"state": job.state}), job.outcome

    def _owned(self, org_id: str, job_handle: str) -> _Job:
        request_id = self.by_handle.get(job_handle)
        job = self.jobs.get(request_id) if request_id else None
        if job is None or job.request.org_id != org_id:
            # Cross-tenant and unknown are indistinguishable on purpose.
            raise errors.NotFound(f"no job {job_handle} owned by org {org_id}")
        return job

    async def prepared(self, job_handle: str, media: tuple[MediaRef, ...] = ()) -> Admission:
        self.failures.before("prepared")
        async with self._lock:
            request_id = self.by_handle.get(job_handle)
            job = self.jobs.get(request_id) if request_id else None
            if job is None:
                raise errors.NotFound(f"no job {job_handle}")
            if job.terminal:
                raise errors.AlreadyTerminal(f"job {job_handle} is {job.state}")
            if job.state is not JobState.preparing:
                raise errors.StateConflict(f"prepared requires preparing, not {job.state}")
            for ref in media:
                if ref.org_id != job.request.org_id:
                    raise errors.Forbidden("prepared media must belong to the job's org")
            now = self.clock.now()
            job.prepared = tuple(media)
            job.state = JobState.queued
            job.queued_at = now
            if job.queue_deadline_at is None:
                # r1 R20/R5: derived once, at the first durable queued transition; a
                # prepublication requeue never moves it, so the queue budget cannot
                # be extended by losing a worker.
                job.admission = job.admission.model_copy(update={
                    "queue_deadline_at": _phase_deadline(now, job.budgets.queue_wait_s,
                                                         job.request.deadline_at)})
            self._release(job, ReservationKind.preparation)
            self._emit(job.id, OutboxKind.inference_dispatch, now,
                       {"job_handle": job_handle, "request_id": job.id})
            admission = job.admission.model_copy(update={"state": job.state})
        self.failures.after_commit("prepared")
        return admission

    def _queue_wait(self, job: _Job, now: datetime) -> timedelta:
        """r1 R5: cumulative from the first durable `queued` transition. A
        prepublication requeue keeps the time already spent."""
        spent = job.queued_elapsed
        if job.queued_at is not None:
            spent += now - job.queued_at
        return spent

    def _release(self, job: _Job, kind: ReservationKind) -> None:
        reservation = job.reservations.get(kind)
        if reservation is not None and reservation.active:
            job.reservations[kind] = reservation.model_copy(update={"active": False})

    async def claim(self, job_id: str, worker_id: str) -> Lease:
        self.failures.before("claim")
        async with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise errors.NotFound(f"no job {job_id}")
            if job.terminal:
                raise errors.AlreadyTerminal(f"job {job_id} is {job.state}")
            now = self.clock.now()
            if job.state is not JobState.queued:
                raise errors.NotClaimable(f"job {job_id} is {job.state}, not queued")
            if now >= job.request.deadline_at:
                raise errors.NotClaimable(f"job {job_id} is past its absolute deadline")
            if job.queue_deadline_at is not None and now >= job.queue_deadline_at:
                # r1 R20: compared against the *persisted* instant, not a budget plus
                # a local clock. A job the customer has already been told to give up
                # on must not start running; `recover` terminalizes it meanwhile.
                raise errors.NotClaimable(f"job {job_id} is past its queue deadline")
            if job.queued_at is not None:
                # Queue wait accumulates across attempts; a requeue must not hand
                # the job a fresh budget (01: no extension through retries).
                job.queued_elapsed += now - job.queued_at
                job.queued_at = None
            job.generation += 1                     # generation from the database clock
            job.state = JobState.running
            generation_deadline_at = _phase_deadline(now, job.budgets.generation_s,
                                                     job.request.deadline_at)
            job.lease = Lease(
                job_id=job_id, generation=job.generation, worker_id=worker_id, acquired_at=now,
                expires_at=now + timedelta(seconds=self.limits.lease_ttl_s),
                # r1 R20: the generation phase starts at the claim, so both instants
                # are derived here and handed to the worker with its lease.
                generation_deadline_at=generation_deadline_at,
                first_token_deadline_at=_phase_deadline(now, job.budgets.first_token_s,
                                                        generation_deadline_at))
            lease = job.lease
        self.failures.after_commit("claim")
        return lease

    async def heartbeat(self, lease: Lease) -> Lease:
        self.failures.before("heartbeat")
        async with self._lock:
            job = self._fence(lease)
            now = self.clock.now()
            job.lease = lease.model_copy(update={
                "expires_at": now + timedelta(seconds=self.limits.lease_ttl_s)})
            return job.lease

    def _fence(self, lease: Lease) -> _Job:
        """Generation, owner, state and lease expiry, compared against durable
        state and the database clock. A fenced worker mutates nothing."""
        job = self.jobs.get(lease.job_id)
        if job is None:
            raise errors.NotFound(f"no job {lease.job_id}")
        if job.terminal:
            raise errors.AlreadyTerminal(f"job {job.id} is already {job.state}")
        if job.state is not JobState.running or job.lease is None:
            raise errors.StaleLease(f"job {job.id} is {job.state} with no active lease")
        if job.generation != lease.generation:
            raise errors.StaleLease(f"generation {lease.generation} != {job.generation}")
        if job.lease.worker_id != lease.worker_id:
            raise errors.StaleLease(f"lease belongs to {job.lease.worker_id}")
        if self.clock.now() >= job.lease.expires_at:
            raise errors.StaleLease(f"lease expired at {job.lease.expires_at}")
        return job

    async def cancel(self, org_id: str, job_handle: str) -> TerminalOutcome:
        self.failures.before("cancel")
        async with self._lock:
            job = self._owned(org_id, job_handle)
            if job.terminal:
                # Completion won the race; a completed job stays completed.
                return job.outcome
            outcome = self._terminalize(job, TerminalCause.client_cancelled, None, None,
                                       JobState.cancelled)
        self.failures.after_commit("cancel")
        return outcome

    async def complete(self, lease: Lease, outcome: TerminalOutcome) -> TerminalOutcome:
        """Settlement is the store's authority: the caller's `settlement_state`
        and `debit` are recomputed, never trusted."""
        self.failures.before("complete")
        if outcome.job_id != lease.job_id:
            # r1 R10: an outcome built for one job never settles another. Without
            # this, job A is settled with job B's usage and debit.
            raise errors.InvalidRequest("the outcome does not belong to the leased job")
        async with self._lock:
            job = self.jobs.get(lease.job_id)
            if job is None:
                raise errors.NotFound(f"no job {lease.job_id}")
            if job.terminal:
                proposal = (outcome.cause, outcome.usage, outcome.result_ref)
                if proposal in (job.proposal,
                                (job.outcome.cause, job.outcome.usage, job.outcome.result_ref)):
                    # The identical completion replays, including when the store
                    # rewrote the settlement (over-envelope usage, missing usage):
                    # the winner's retry must see the committed outcome, not a
                    # conflict it cannot act on.
                    return job.outcome
                raise errors.AlreadyTerminal(f"job {job.id} already settled as {job.outcome.cause}")
            self._fence(lease)
            settled = self._terminalize(job, outcome.cause, outcome.usage, outcome.result_ref,
                                        outcome.state)
            job.proposal = (outcome.cause, outcome.usage, outcome.result_ref)
        self.failures.after_commit("complete")
        return settled

    def _terminalize(self, job: _Job, cause: TerminalCause, usage: Usage | None,
                     result_ref: str | None, state: JobState) -> TerminalOutcome:
        if state not in TERMINAL_STATES:
            raise errors.StateConflict(f"{state} is not terminal")
        if state not in states_for_cause(cause):
            # Validate the pair *before* the wallet moves. Building the record last
            # would otherwise debit the customer and then raise, leaving money moved
            # on a job that never became terminal.
            raise errors.StateConflict(f"cause {cause} cannot carry state {state}")
        if usage is not None and usage.certainty is not UsageCertainty.authoritative:
            raise errors.InvalidRequest("a present usage must be authoritative")
        now = self.clock.now()
        hold = self.holds[job.id]
        wallet = self.wallet(job.request.org_id)

        if usage is None and cause is TerminalCause.completed and not job.published:
            # A delivered success with no authoritative usage and nothing published
            # is not a free success: the engine never reported what it produced, so
            # the honest outcome is an incomplete engine run (02: platform-caused
            # failures are free, and output chunks never bill).
            cause, state, result_ref = TerminalCause.engine_incomplete, JobState.failed, None

        over_envelope = usage is not None and (
            usage.prompt_tokens > job.request.max_input_tokens
            or usage.completion_tokens > job.request.max_output_tokens)
        debit = money.ZERO
        reconcile_after = None
        if usage is None and job.published:
            # Published output but no authoritative usage: the hold stays held for
            # reconciliation and is released as platform-absorbed after the fenced
            # 24h, whatever the cause. 02 wants the internal cost reconciled; late
            # evidence never becomes a delayed customer debit.
            settlement = SettlementState.held_unknown
            hold.state = HoldState.unknown
            reconcile_after = now + timedelta(seconds=self.limits.unknown_usage_reconcile_s)
            hold.reconcile_after = reconcile_after
        elif cause not in BILLABLE_CAUSES or usage is None:
            # r1 R21: only `completed`, `client_cancelled` and `client_disconnected`
            # with authoritative usage settle a debit. Everything else - our
            # deadlines included - is absorbed. `released_free` is "the customer was
            # never going to be charged" (nothing ran, invalid input, the queue
            # expired); `released_platform_absorbed` is "we did the work and ate the
            # cost", which is what a `sync_deadline` or `deadline_exceeded` after
            # real generation is.
            never_charged = cause in FREE_CAUSES or (usage is None and not job.published
                                                     and cause in BILLABLE_CAUSES)
            settlement = (SettlementState.released_free if never_charged
                          else SettlementState.released_platform_absorbed)
            self._release_hold(wallet, hold)
        else:
            candidate = job.admission.price_snapshot.debit(usage.prompt_tokens,
                                                           usage.completion_tokens)
            if over_envelope or candidate > hold.amount:
                # A protocol violation beyond the reserved envelope is a platform
                # failure to reconcile, never an unreserved customer debit.
                cause, state = TerminalCause.platform_error, JobState.failed
                settlement = SettlementState.released_platform_absorbed
                usage, result_ref = None, None
                self._release_hold(wallet, hold)
            elif candidate == 0:
                settlement = SettlementState.released_free
                self._release_hold(wallet, hold)
            else:
                debit = candidate
                wallet.reserved_total = wallet.reserved_total - hold.amount
                wallet.ledger_total = wallet.ledger_total - debit
                hold.state = HoldState.settled
                settlement = SettlementState.settled

        job.state = state
        job.lease = None                            # terminalization fences execution
        job.outcome = TerminalOutcome(
            job_id=job.id, state=state, cause=cause, usage=usage, result_ref=result_ref,
            settlement_state=settlement, debit=debit, settled_at=now,
            reconcile_after=reconcile_after)
        if self.stream is not None:
            # Same transaction as the outcome, the usage, the ledger settlement and
            # the capacity releases: after this commit a settled job always has its
            # terminal event, and the write cannot fail for want of journal budget.
            self.stream.write_terminal(job, job.outcome)
        for kind in ReservationKind:
            self._release(job, kind)
        self.journal.release_reservation(job.id)
        record = self.idem.get((job.request.org_id, job.admission.operation,
                                job.admission.idempotency_key))
        if record is not None and record.request_id == job.id:
            # Tombstone retained for at least 24h after terminal state.
            record.expires_at = now + timedelta(seconds=self.limits.idempotency_ttl_s)
        self._emit(job.id, OutboxKind.usage_projection, now,
                   {"request_id": job.id, "settlement_state": settlement.value})
        self._emit(job.id, OutboxKind.trace_projection, now, {"request_id": job.id})
        return job.outcome

    def _release_hold(self, wallet: _Wallet, hold: _Hold) -> None:
        if hold.state in (HoldState.held, HoldState.unknown):
            wallet.reserved_total = wallet.reserved_total - hold.amount
            hold.state = HoldState.released
            hold.reconcile_after = None

    async def recover(self) -> tuple[object, ...]:
        """Requeue only prepublication attempts, terminalize the rest, release aged
        unknown-usage holds. Returns the index events and outcomes it produced.

        r1 R7: no caller time. The only clock is the injected one that stands for
        the database clock inside the transaction, so nothing a caller passes can
        release an unknown-usage hold before its 24 h window."""
        self.failures.before("recover")
        async with self._lock:
            now = self.clock.now()
            produced: list[object] = []
            for job in list(self.jobs.values()):
                if job.terminal:
                    continue
                produced.extend(self._recover_job(job, now))
            produced.extend(self._release_aged_unknown_holds(now))
            return tuple(produced)

    def _recover_job(self, job: _Job, now: datetime) -> list[object]:
        if now >= job.request.deadline_at:
            cause = (TerminalCause.queue_wait_expired if job.state is JobState.queued
                     else TerminalCause.deadline_exceeded)
            state = JobState.expired if job.state is JobState.queued else JobState.failed
            if job.state is JobState.preparing:
                cause, state = TerminalCause.preparation_failed, JobState.failed
            return [self._terminalize(job, cause, None, None, state)]
        if job.state is JobState.queued and job.queue_deadline_at is not None:
            # r1 R20: the persisted instant, set once at the first queued transition.
            if now >= job.queue_deadline_at:
                return [self._terminalize(job, TerminalCause.queue_wait_expired, None, None,
                                          JobState.expired)]
        if (job.state is JobState.running and job.lease is not None
                and now >= job.lease.generation_deadline_at):
            # r1 R20: the generation phase has its own persisted instant. Past it the
            # attempt is over whether or not its lease is still live, and the cause is
            # our deadline, which r1 R21 makes platform-absorbed.
            return [self._terminalize(job, TerminalCause.deadline_exceeded, None, None,
                                      JobState.failed)]
        if job.state is JobState.running and job.lease is not None and now >= job.lease.expires_at:
            if job.published:
                # After the publication marker, never regenerate: fail honestly.
                return [self._terminalize(job, TerminalCause.lost_after_publication, None, None,
                                          JobState.failed)]
            if job.attempts >= self.limits.max_prepublication_retries:
                return [self._terminalize(job, TerminalCause.retries_exhausted, None, None,
                                          JobState.failed)]
            job.attempts += 1
            job.state = JobState.queued
            job.lease = None
            job.queued_at = now
            event = IndexEvent(event_id=self.ids.event_id(), job_id=job.id,
                               org_id=job.request.org_id, key_id=job.request.key_id,
                               execution_mode=job.request.execution_mode, available_at=now,
                               attempt=job.attempts)
            self._emit(job.id, OutboxKind.inference_dispatch, now, {"request_id": job.id,
                                                                    "attempt": job.attempts})
            return [event]
        return []

    def _release_aged_unknown_holds(self, now: datetime) -> list[object]:
        released = []
        for hold in self.holds.values():
            if hold.state is not HoldState.unknown or hold.reconcile_after is None:
                continue
            job = self.jobs[hold.request_id]
            fenced = job.lease is None                       # terminalization cleared it
            if not (job.terminal and fenced and now >= hold.reconcile_after):
                continue
            self._release_hold(self.wallet(hold.org_id), hold)
            job.outcome = job.outcome.model_copy(update={
                "settlement_state": SettlementState.released_platform_absorbed,
                "reconcile_after": None})
            self._emit(job.id, OutboxKind.usage_projection, now,
                       {"request_id": job.id, "settlement_state": "released_platform_absorbed"})
            released.append(job.outcome)
        return released


class FakeStreamStore:
    """`ports.StreamStore`: the PostgreSQL output journal. Commit, then relay."""

    def __init__(self, jobs: FakeJobStore, *, failures: FailurePlan | None = None) -> None:
        self.jobs = jobs
        self.clock = jobs.clock
        self.limits = jobs.limits
        self.failures = failure_hooks(failures)
        self.chunks: dict[str, list[Chunk]] = {}
        self.pruned_to: dict[str, tuple[int, int]] = {}
        self.expired_jobs: set[str] = set()
        jobs.stream = self          # one database: settlement writes the terminal event

    @staticmethod
    def event_bytes(event: EngineEvent) -> int:
        return len(compact_bytes(event.payload))

    async def append(self, lease: Lease, events: tuple[EngineEvent, ...]) -> tuple[Chunk, ...]:
        self.failures.before("append")
        async with self.jobs._lock:
            job = self.jobs._fence(lease)
            now = self.clock.now()
            sizes = [self.event_bytes(event) for event in events]
            for size in sizes:
                if size > self.limits.journal_event_max_bytes:
                    raise errors.JournalWriteFailed(
                        f"event of {size} bytes exceeds {self.limits.journal_event_max_bytes}")
            self.jobs.journal.store(job.id, sum(sizes))
            stored = self.chunks.setdefault(job.id, [])
            sequence = max((chunk.sequence for chunk in stored
                            if chunk.generation == lease.generation), default=0)
            committed = []
            for event, size in zip(events, sizes):
                sequence += 1
                chunk = Chunk(job_id=job.id, generation=lease.generation, sequence=sequence,
                              event_type=event.type, payload=event.payload, bytes=size,
                              persisted_at=now,
                              expires_at=now + timedelta(seconds=self.limits.journal_chunk_ttl_s))
                stored.append(chunk)
                committed.append(chunk)
            # The first committed chunk establishes output ownership: from here on
            # the request is never regenerated. An empty batch commits nothing, so
            # it must not forbid a prepublication requeue.
            if committed:
                job.published = True
        self.failures.after_commit("append")
        return tuple(committed)

    async def read_owned(self, org_id: str, job_handle: str, cursor: Cursor | None = None,
                         limit: int = 100) -> tuple[tuple[Chunk, ...], Cursor | None]:
        job = self.jobs._owned(org_id, job_handle)
        if not isinstance(limit, int) or limit <= 0:
            raise errors.InvalidRequest(f"limit must be a positive integer, not {limit!r}")
        limit = min(limit, MAX_READ_LIMIT)
        if job.id in self.expired_jobs:
            raise errors.JournalExpired(f"journal for {job_handle} has expired")
        position = (cursor.generation, cursor.sequence) if cursor else (0, 0)
        pruned_to = self.pruned_to.get(job.id)
        if pruned_to is not None and position < pruned_to:
            raise errors.ReplayGap(f"events up to {pruned_to} are no longer retained")
        available = sorted((chunk for chunk in self.chunks.get(job.id, [])
                            if (chunk.generation, chunk.sequence) > position),
                           key=lambda chunk: (chunk.generation, chunk.sequence))
        page = tuple(available[:limit])
        next_cursor = page[-1].cursor if page else cursor
        return page, next_cursor

    async def finalize_in_transaction(self, outcome: TerminalOutcome) -> Chunk:
        """Read back (or, for an outcome settled without this journal, write) the
        terminal event of the settling transaction. Never the only chance to write
        it: `JobStore` already did, where the money moved."""
        job = self.jobs.jobs.get(outcome.job_id)
        if job is None:
            raise errors.NotFound(f"no job {outcome.job_id}")
        if not job.terminal:
            raise errors.StateConflict("finalize_in_transaction runs inside the settling "
                                       "transaction, after the terminal outcome")
        return self.write_terminal(job, outcome)

    def write_terminal(self, job: _Job, outcome: TerminalOutcome) -> Chunk:
        """Idempotent, synchronous, and never refused: it runs inside the settling
        transaction, so a byte-budget rejection here would leave a settled job
        without its terminal event."""
        stored = self.chunks.setdefault(job.id, [])
        for chunk in stored:
            if chunk.event_type is ChunkEventType.terminal:
                return chunk                        # one terminal event, replay safe
        generation = max((chunk.generation for chunk in stored), default=job.generation) or 1
        sequence = max((chunk.sequence for chunk in stored if chunk.generation == generation),
                       default=0) + 1
        now = self.clock.now()
        payload = {"state": outcome.state.value, "cause": outcome.cause.value,
                   "settlement_state": outcome.settlement_state.value}
        chunk = Chunk(job_id=job.id, generation=generation, sequence=sequence,
                      event_type=ChunkEventType.terminal, payload=payload,
                      bytes=len(compact_bytes(payload)), persisted_at=now,
                      expires_at=now + timedelta(seconds=self.limits.journal_chunk_ttl_s))
        stored.append(chunk)
        self.jobs.journal.store(job.id, chunk.bytes, settling=True)
        return chunk

    async def expire(self, now: datetime | None = None) -> int:
        # r1 R7: a caller-supplied time is a bound at most. Pruning never runs ahead
        # of the database clock, so a future argument cannot expire a live journal.
        now = min(now, self.clock.now()) if now is not None else self.clock.now()
        removed = 0
        for job_id, stored in list(self.chunks.items()):
            keep, drop = [], []
            for chunk in stored:
                (drop if now >= chunk.expires_at else keep).append(chunk)
            if not drop:
                continue
            removed += len(drop)
            self.pruned_to[job_id] = max((chunk.generation, chunk.sequence) for chunk in drop)
            self.jobs.journal.prune(job_id, sum(chunk.bytes for chunk in drop))
            if keep:
                self.chunks[job_id] = keep
            else:
                del self.chunks[job_id]
                self.expired_jobs.add(job_id)
        return removed
