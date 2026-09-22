"""W2: one fenced attempt, from a claimed lease to a settled outcome.

    runner = AttemptRunner(jobs=store, stream=journal, engine=engine, clock=clock,
                           worker_id="worker-a", count_prompt_tokens=..., put_result=...)
    result = await runner.run(job_id)

What this module is responsible for, and what it deliberately is not:

* **Fencing (`02` §5, r1 R29/R46).** Every store call carries the lease: `load_work`,
  `append`, `heartbeat` and `complete`. A `stale_lease` means "stop, the winner is
  somebody else" and settles **nothing**; an `already_terminal` means the store
  terminalized the job in that same call (a customer cancellation, or a phase deadline
  R29 enforced) and there is nothing left to settle either. The two refusals are
  different and this module branches on both.
* **Persist before relay (`02` §6).** A delta is journalled through the StreamStore and
  only the **committed** chunks are handed to the relay. A journal write that fails - or
  that cannot be confirmed - stops execution and relays nothing, because a chunk a
  customer saw and the journal never took is output we can no longer prove.
* **`visible` only (r1 R58).** The journal and every customer-facing relay carry the
  filtered text. `raw` is trace capture's, `content` is a transitional alias of `raw`,
  and a relay that reads either leaks the model's reasoning block.
* **Exact accounting (r1 R21/R30).** Usage reaches `complete` only when the stream's own
  usage event says it is authoritative; otherwise it is `None` and the store's
  unknown-usage reconciliation takes it. `completed` needs a finished stream **and**
  authoritative usage **and** no line we could not read, re-checked here rather than
  taken from the adapter's advisory `terminal_cause`, because that is the field that
  decides whether a customer is charged.
* **A task-level deadline.** The engine adapter's bounds are per event and per phase, and
  its overshoot is one chunk interval (W1 limit); the whole attempt is bounded here by
  `asyncio.timeout` against the lease's persisted `generation_deadline_at`.
* **Not** claiming from the index, concurrency or drain: that is `loop.py`. Not
  preparation: a preparation lease fences M's media work (r1 R46), and this runner
  refuses one rather than pretending to execute it.

The engine is used through `ports.Engine` alone - events, and nothing else - so the
shared `FakeEngine` and W1's `VllmEngine` are interchangeable here. When the iterator
also exposes W1's `terminal_cause`, it is read as a *hint* and re-checked; when it does
not, the outcome is derived from the canonical events and the lease's own instants.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import (ChunkEventType, EngineEvent, JobState, Lease, LeaseKind,
                                 SettlementState, TerminalCause, TerminalOutcome, Usage,
                                 UsageCertainty, Work)
from .engine import EngineFailure, EngineProtocolViolation, prepared_request

# A finish the engine reports that means "this answer is whole". Anything else - `abort`,
# `content_filter`, nothing at all - is not a completion, whatever else the stream did.
FINISHED_REASONS = ("stop", "length")
# One append per event is a transaction per token; one append per stream is an unbounded
# buffer. `02` §6 bounds the batch by time (50 ms, `stream_batch_ms`); this bounds it by
# count as well, so a burst that arrives inside one clock tick is still committed in
# pieces and the memory held between commits is bounded.
BATCH_MAX_EVENTS = 32


class _JournalFailed(RuntimeError):
    """An append that failed, or that we cannot prove either way. Private: it never
    leaves this module - the caller settles `journal_write_failed`."""

    def __init__(self, detail: str, *, committed: bool) -> None:
        self.detail = detail
        # True when the batch may already be in the journal (the write was acknowledged
        # and then the answer was lost): the job may be published, so it can no longer be
        # regenerated and `02` §6 makes the honest outcome a terminal failure.
        self.committed = committed
        super().__init__(detail)


class _Terminalized(RuntimeError):
    """The store terminalized the job inside one of our own fenced calls (a customer
    cancellation, or R29's phase deadline). Private control flow: it carries the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class AttemptResult:
    """What one attempt did. `outcome` is the store's settled record, and it is `None`
    whenever this attempt settled nothing - which is the normal answer for a lost fence,
    for a job somebody else terminalized, and for an attempt a drain cancelled."""

    job_id: str
    generation: int = 0
    outcome: TerminalOutcome | None = None
    cause: TerminalCause | None = None           # what the store settled, never our proposal
    proposed_cause: TerminalCause | None = None  # what this attempt asked for
    refusal: str | None = None                   # the error code when nothing was settled
    committed: int = 0                           # journal chunks committed
    relayed: int = 0                             # chunks handed to the relay, after commit
    deltas: int = 0                              # delta events received from the engine
    heartbeats: int = 0
    visible_text: str = ""
    usage_unknown_reason: str | None = None
    cancelled: bool = False                      # the store terminalized it under us
    engine_cancel: bool | None = None            # what `Engine.cancel` answered, if called
    detail: str = ""

    @property
    def settled(self) -> bool:
        return self.outcome is not None


@dataclass
class _State:
    """Everything one attempt accumulates. Kept apart from `AttemptResult` because the
    result is what a caller reads and this is what the run needs."""

    lease: Lease
    last_renew: datetime
    batch: list[EngineEvent] = field(default_factory=list)
    batch_opened_at: datetime | None = None
    usage: Usage | None = None                   # authoritative only, from the usage event
    usage_events: int = 0
    last_event: ChunkEventType | None = None
    cancelled: bool = False
    stream: Any = None


class AttemptRunner:
    """One attempt at a time. A runner is reused across attempts; nothing is kept
    between them, because a retry is a new generation from the store (r1 R46)."""

    def __init__(self, *, jobs, stream, engine, clock, worker_id: str,
                 count_prompt_tokens: Callable[[Work], Any],
                 put_result: Callable[[str, str], Any],
                 relay: Callable[[tuple], Any] | None = None,
                 limits: PilotSettings = DEFAULTS) -> None:
        self.jobs = jobs                     # ports.JobStore
        self.stream = stream                 # ports.StreamStore
        self.engine = engine                 # ports.Engine
        self.clock = clock
        self.worker_id = worker_id
        # Preparation's **exact** prompt count. An argument rather than a guess: `Work`
        # carries no count, and `02` forbids estimating one - a wrong number changes the
        # context check and the zero-output cancellation usage (W1 limit 7). Required, so
        # no code path can invent one.
        self.count_prompt_tokens = count_prompt_tokens
        # `02` §7: the immutable final result object is stored **before** the settling
        # transaction, and r1 R30 refuses a `succeeded` outcome without its reference.
        self.put_result = put_result
        self.relay = relay
        self.limits = limits

    # --- the attempt ----------------------------------------------------------
    async def run(self, job_id: str) -> AttemptResult:
        """Claim the job and execute it. A claim that loses (another worker won, the
        queue deadline passed, the job is terminal) is reported, never settled."""
        try:
            lease = await self.jobs.claim(job_id, self.worker_id)
        except errors.DomainError as refused:
            return AttemptResult(job_id=job_id, refusal=refused.code, detail=str(refused))
        return await self.execute(lease)

    async def execute(self, lease: Lease) -> AttemptResult:
        if lease.kind is not LeaseKind.inference:
            # r1 R46: a preparation lease fences M's media work. Executing under one would
            # append output to a job that is still `preparing`.
            raise errors.InvalidRequest(f"{lease.kind} lease cannot execute an attempt")
        result = AttemptResult(job_id=lease.job_id, generation=lease.generation)
        state = _State(lease=lease, last_renew=self.clock.now())
        try:
            work = await self.jobs.load_work(lease)
        except errors.DomainError as refused:
            # `stale_lease`: stop and let the winner run. `already_terminal`: the store
            # settled it in this call. Neither settles anything here (W1 handback), and
            # neither cancels the engine - nothing has been generated yet.
            result.refusal = refused.code
            result.detail = str(refused)
            result.cancelled = isinstance(refused, errors.AlreadyTerminal)
            return result
        try:
            prepared = prepared_request(work, await self._prompt_tokens(work),
                                        limits=self.limits)
        except errors.DomainError as refused:
            result.detail = str(refused)
            return await self._settle(state, result, _cause_for(refused))
        except Exception as failure:                 # a tokenizer that blew up is ours
            result.detail = f"{type(failure).__name__}: {failure}"
            return await self._settle(state, result, TerminalCause.platform_error)
        return await self._generate(state, result, prepared)

    async def _generate(self, state: _State, result: AttemptResult, prepared) -> AttemptResult:
        stream = self.engine.generate(state.lease, prepared)
        state.stream = stream
        cause: TerminalCause | None = None
        try:
            try:
                # W1 limit: the adapter checks its bounds when a chunk arrives, so a silent
                # engine overshoots by one chunk interval. The attempt as a whole is bounded
                # here, against the instant the store persisted with the lease (r1 R20).
                async with asyncio.timeout(self._remaining(state.lease)):
                    async for event in stream:
                        await self._absorb(state, result, event)
                    await self._flush(state, result, final=True)
            finally:
                # Deterministic: stop reading the engine now, whatever ended the loop.
                await self._aclose(stream)
        except TimeoutError:
            cause = TerminalCause.deadline_exceeded
            result.detail = "the attempt passed its generation deadline"
        except _Terminalized as settled_elsewhere:
            result.refusal = settled_elsewhere.code
            result.cancelled = True
            return result
        except errors.StaleLease as lost:
            # Never settle on a lost fence: the generation that replaced ours owns the job.
            result.refusal = lost.code
            result.detail = str(lost)
            return result
        except errors.AlreadyTerminal as settled_elsewhere:
            result.refusal = settled_elsewhere.code
            result.cancelled = True
            result.detail = str(settled_elsewhere)
            return result
        except _JournalFailed as failed:
            # `02`: stop execution and relay, then fail or reconcile safely. Nothing of
            # this batch was relayed, because relay only ever sees committed chunks.
            cause = TerminalCause.journal_write_failed
            result.detail = failed.detail
        except EngineFailure as failure:
            cause = failure.terminal_cause          # transport / error / incomplete / protocol
            result.detail = f"{type(failure).__name__}: {failure.detail}"
        except errors.DomainError as refused:
            # The adapter refuses at the first `__anext__`, not when `generate` is called,
            # so a media or parameter refusal arrives here: same classification as above.
            cause = _cause_for(refused)
            result.detail = str(refused)
        except Exception as failure:                # nothing untyped becomes a settlement
            cause = TerminalCause.platform_error
            result.detail = f"{type(failure).__name__}: {failure}"
        if cause is None:
            cause = self._cause(state, result, stream)
        return await self._settle(state, result, cause)

    # --- events ---------------------------------------------------------------
    async def _absorb(self, state: _State, result: AttemptResult, event: EngineEvent) -> None:
        state.last_event = event.type
        journal = self._journal_event(event)
        if event.type is ChunkEventType.delta:
            result.deltas += 1
            result.visible_text += self._visible(event)
        elif event.type is ChunkEventType.usage:
            self._note_usage(state, result, event)
        if journal is not None:
            if state.batch_opened_at is None:
                state.batch_opened_at = self.clock.now()
            state.batch.append(journal)
        if self._batch_due(state):
            await self._flush(state, result)
        await self._maybe_heartbeat(state, result)

    def _visible(self, event: EngineEvent) -> str:
        """r1 R58: a delta carries `visible`, the customer's text after the reasoning
        filter. A delta without it is a protocol breach and not an empty answer: relaying
        `raw` (or its `content` alias) would publish the reasoning block, and silently
        relaying nothing would deliver a truncated answer as a whole one."""
        visible = event.payload.get("visible")
        if not isinstance(visible, str):
            raise EngineProtocolViolation("a delta event carries no visible text (R58)",
                                          payload=sorted(event.payload))
        return visible

    def _journal_event(self, event: EngineEvent) -> EngineEvent | None:
        """What goes in the journal: `visible` only for a delta, the event as it stands
        otherwise. r1 R30 forbids a worker appending a terminal event, so one never gets
        built here - the store derives it from the stored outcome."""
        if event.type is ChunkEventType.terminal:
            raise EngineProtocolViolation("a worker may not journal a terminal event (R30)")
        if event.type is not ChunkEventType.delta:
            return event
        visible = self._visible(event)
        if not visible:
            # Nothing for the customer (an empty first chunk, or a delta that was entirely
            # reasoning): journalling it would spend journal bytes on nothing.
            return None
        return EngineEvent(type=ChunkEventType.delta, payload={"visible": visible})

    def _note_usage(self, state: _State, result: AttemptResult, event: EngineEvent) -> None:
        """r1 R58/R21: usage is authoritative only when the event says so, and one stream
        carries at most one usage event. A second one means the engine contradicted
        itself, so the count is unknown and D reconciles rather than billing a guess."""
        state.usage_events += 1
        reason = event.payload.get("reason")
        if event.usage is None or event.usage.certainty is not UsageCertainty.authoritative:
            state.usage = None
            result.usage_unknown_reason = str(reason) if reason else "unknown"
            return
        if state.usage_events > 1:
            state.usage = None
            result.usage_unknown_reason = "multiple_usage_events"
            return
        state.usage = event.usage

    # --- the journal ----------------------------------------------------------
    def _batch_due(self, state: _State) -> bool:
        if not state.batch:
            return False
        if len(state.batch) >= BATCH_MAX_EVENTS:
            return True
        opened = state.batch_opened_at
        elapsed_ms = (self.clock.now() - opened).total_seconds() * 1000 if opened else 0.0
        return elapsed_ms >= self.limits.stream_batch_ms

    async def _flush(self, state: _State, result: AttemptResult, *, final: bool = False) -> None:
        """Commit the batch, **then** relay it. The order is the whole point: a chunk the
        customer has seen and the journal has not taken can never be replayed."""
        if not state.batch:
            return
        events = tuple(state.batch)
        try:
            # Fenced like every other mutation, and it is also the cancellation poll while
            # output is flowing: a customer cancellation is discovered within one batch.
            chunks = await self._fenced(self.stream.append(state.lease, events), state, result)
        except (errors.StaleLease, _Terminalized):
            raise                                   # a fence loss is not a write failure
        except errors.DomainError as failed:
            raise _JournalFailed(f"{failed.code}: {failed}", committed=False) from None
        except Exception as failed:
            # The write was acknowledged and the answer lost, or something unknown broke:
            # either way we cannot prove what is in the journal, so we do not relay it.
            raise _JournalFailed(f"{type(failed).__name__}: {failed}", committed=True) from None
        state.batch.clear()
        state.batch_opened_at = None
        result.committed += len(chunks)
        await self._relay(result, chunks)
        if final:
            state.batch_opened_at = None

    async def _relay(self, result: AttemptResult, chunks: tuple) -> None:
        if self.relay is None or not chunks:
            return
        try:
            await _maybe_await(self.relay(chunks))
        except Exception as failure:
            # The chunks are durable; a relay that dropped them is a delivery problem the
            # customer resolves with the retained cursor (`02`, "Output and unknown
            # outcomes"). It must not fail an attempt whose output is committed.
            result.detail = f"relay failed: {type(failure).__name__}: {failure}"
            return
        result.relayed += len(chunks)

    # --- the lease ------------------------------------------------------------
    async def _maybe_heartbeat(self, state: _State, result: AttemptResult) -> None:
        """Renew inside the TTL, and read the **renewed stored lease** back: r1 R29 makes
        the caller's copy a fencing token, so the store's answer is the one to fence with.

        ponytail: event-driven, so a phase with no events at all is covered by the
        adapter's own TTFT/stall bounds (60 s and 20 s against a 120 s TTL), not by this.
        A background renewal task is the upgrade path if a phase can ever outlast the TTL
        without producing an event. This is also the cancellation poll: while output is
        flowing `append` discovers a cancelled job within one batch, and while it is not,
        this call does.
        """
        if (self.clock.now() - state.last_renew).total_seconds() < self.limits.lease_heartbeat_s:
            return
        state.lease = await self._fenced(self.jobs.heartbeat(state.lease), state, result)
        state.last_renew = self.clock.now()
        result.heartbeats += 1

    async def _fenced(self, awaitable, state: _State, result: AttemptResult):
        """Run one fenced call, turning the store's terminalization into the private
        control-flow signal - and telling the engine to stop while the stream is still
        live, which is what makes a customer cancellation reach it (W2 acceptance)."""
        try:
            return await awaitable
        except errors.AlreadyTerminal as settled_elsewhere:
            await self._cancel_engine(state, result)
            raise _Terminalized(settled_elsewhere.code) from None

    async def _cancel_engine(self, state: _State, result: AttemptResult) -> None:
        """r1 R58: cancel with the **current** lease only - intents are keyed by
        `(job_id, generation)`, so cancelling with any other lease would either do nothing
        or stop an attempt that replaced ours. On a lost fence we do not call this at all.

        `False` means the engine could not record the intent (W1 limit: a full map, or the
        window between `generate()` and the first `__anext__`). We own the task, so we
        stop it ourselves either way - the caller's `finally` closes the stream.
        """
        try:
            result.engine_cancel = await self.engine.cancel(state.lease)
        except Exception as failure:                 # best effort; closing the stream stops it
            result.engine_cancel = False
            result.detail = f"engine cancel failed: {type(failure).__name__}: {failure}"
        state.cancelled = True

    def _remaining(self, lease: Lease) -> float:
        return max(0.0, (lease.generation_deadline_at - self.clock.now()).total_seconds())

    async def _aclose(self, stream) -> None:
        closer = getattr(stream, "aclose", None)
        if closer is not None:
            await closer()

    async def _prompt_tokens(self, work: Work) -> int:
        return await _maybe_await(self.count_prompt_tokens(work))

    # --- the outcome ----------------------------------------------------------
    def _cause(self, state: _State, result: AttemptResult, stream) -> TerminalCause:
        """The cause a clean end deserves.

        W1's `terminal_cause` is advisory (the store recomputes settlement, r1 R21/R30),
        and an iterator that does not have one is answered from the canonical events and
        the lease's own persisted instants. Either way `completed` is re-checked here,
        because it is the answer that decides whether a customer is charged.
        """
        hint = getattr(stream, "terminal_cause", None)
        cause = hint if isinstance(hint, TerminalCause) else self._derived_cause(state)
        if cause is TerminalCause.completed and not self._whole(state, stream):
            # A finished stream whose usage we do not know, or that carried a line we could
            # not read, is an incomplete engine run - which is what D does with a delivered
            # success that has no authoritative count.
            result.detail = result.detail or "the stream finished without a whole answer"
            return TerminalCause.engine_incomplete
        return cause

    def _derived_cause(self, state: _State) -> TerminalCause:
        if state.cancelled:
            return TerminalCause.client_cancelled
        if self.clock.now() >= state.lease.generation_deadline_at:
            return TerminalCause.deadline_exceeded
        if state.usage is not None and state.last_event is ChunkEventType.usage:
            # r1 R58: authoritative usage is the last usage object of the stream and arrives
            # after the final content delta, so an authoritative usage event that *is* the
            # last event is the engine saying it finished.
            return TerminalCause.completed
        return TerminalCause.engine_incomplete

    def _whole(self, state: _State, stream) -> bool:
        """The three facts `completed` needs (W1 handback): the engine finished, it told
        us what it used, and no line was dropped on the way."""
        if state.usage is None:
            return False
        if getattr(stream, "malformed_lines", 0):
            return False
        finish = getattr(stream, "finish_reason", None)
        return finish is None or finish in FINISHED_REASONS

    async def _settle(self, state: _State, result: AttemptResult,
                      cause: TerminalCause) -> AttemptResult:
        """`02` §7: the immutable result object, then the one settling transaction."""
        result.proposed_cause = cause
        result_ref = None
        if cause is TerminalCause.completed:
            try:
                result_ref = await _maybe_await(self.put_result(state.lease.job_id,
                                                                result.visible_text))
            except Exception as failure:
                # r1 R30: a success the customer cannot fetch is not a success. Nothing was
                # stored, so this is ours to absorb, not theirs to be charged for.
                cause, result_ref = TerminalCause.platform_error, None
                result.proposed_cause = cause
                result.detail = f"result store failed: {type(failure).__name__}: {failure}"
        usage = state.usage
        if usage is not None and usage.certainty is not UsageCertainty.authoritative:
            # The store refuses a non-authoritative usage outright, which would lose the
            # settlement; unknown usage has a reconciliation path and this has none.
            usage, result.usage_unknown_reason = None, result.usage_unknown_reason or "not_authoritative"
        outcome = TerminalOutcome(
            job_id=state.lease.job_id, state=_state_for(cause), cause=cause, usage=usage,
            result_ref=result_ref,
            # The store recomputes both of these; they are required fields, not a proposal
            # it reads (`complete`: "the caller's settlement_state and debit are
            # recomputed, never trusted").
            settlement_state=SettlementState.released_free,
            settled_at=self.clock.now())
        try:
            settled = await self.jobs.complete(state.lease, outcome)
        except (errors.StaleLease, errors.AlreadyTerminal) as refused:
            result.refusal = refused.code
            result.cancelled = result.cancelled or isinstance(refused, errors.AlreadyTerminal)
            return result
        except Exception as failure:
            # The terminal acknowledgment was lost. The **identical** completion replays
            # (`complete` is idempotent for the same proposal), so one retry either learns
            # the committed outcome or leaves the job to `recover`. A *different* outcome is
            # never proposed, because that is how a job settles twice.
            result.detail = f"terminal ack lost: {type(failure).__name__}: {failure}"
            try:
                settled = await self.jobs.complete(state.lease, outcome)
            except errors.DomainError as refused:
                result.refusal = refused.code
                return result
            except Exception as again:
                result.refusal = "settlement_unknown"
                result.detail = f"{result.detail}; retry failed: {type(again).__name__}: {again}"
                return result
        result.outcome = settled
        result.cause = settled.cause
        return result


def _cause_for(refused: errors.DomainError) -> TerminalCause:
    """The cause a refusal at the engine boundary deserves.

    `unsupported_media` is `02`'s `invalid_media`, which is free: the media was never
    usable, so nothing ran and nobody is charged. Anything else - an unsupported
    parameter, a context length, a reused lease - is a request admission should have
    refused, which makes it ours to absorb rather than the customer's to be charged for.
    Either way the customer pays nothing; the two differ in which of us it is recorded
    against (`released_free` versus `released_platform_absorbed`).
    """
    if isinstance(refused, errors.UnsupportedMedia):
        return TerminalCause.invalid_media
    return TerminalCause.platform_error


def _state_for(cause: TerminalCause) -> JobState:
    """The one state that cause may carry here. `records.CAUSE_STATES` allows two for
    some causes; a worker only ever produces these three, and the store refuses a pair
    that does not belong together."""
    if cause is TerminalCause.completed:
        return JobState.succeeded
    if cause is TerminalCause.client_cancelled:
        return JobState.cancelled
    return JobState.failed


async def _maybe_await(value):
    """The injected collaborators may be sync (a tokenizer) or async (an object store)."""
    if inspect.isawaitable(value):
        return await value
    return value
