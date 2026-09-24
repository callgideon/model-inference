"""G2: durable acceptance, the synchronous wait and the persistent SSE relay.

    relay = Relay(jobs=store, stream=journal, media=media_store, regime="credit", ...)
    deps = IngressDeps(accept=relay.accept, catalog=..., ...)

`accept(auth, request, idem)` is the ingress's seam (G1R request 1). It does the durable
part - media preparation, staging, one admission transaction, attaching the staged refs -
and returns an ASGI response that owns the accepted job from then on:

* **sync** (`_Answer`): nothing is sent until the job's terminal outcome is committed, then
  the result (`chat_success_nonstream.json`) or the envelope its cause maps to.
* **SSE** (`_Stream`): headers only after durable acceptance; the first frame names the job
  (`infrx.progress` with `job_handle` and `request_id`, the identity a client resumes a lost
  connection with); every model frame after it is a **committed** journal chunk read back
  through `StreamStore.read_owned`, `visible` text only (R58/R80); keepalives are SSE
  comments; `[DONE]` only once a terminal commit is known.

Both own the job until it is terminal. A client that leaves, a wait past the job's own
deadline, or a relay that cannot go on cancels the job durably, and the cancel is shielded
from the handler's own cancellation - so no execution is left running for nobody. The one
exception is deliberate: an SSE response cancelled from outside (the process stopping)
after the identity frame leaves the job to its worker, because the client holds a handle
it can resume with (G3's events route).

The responses are Starlette `Response`s with their own `__call__` rather than a
`StreamingResponse` around a generator: the whole lifetime is one `try`/`finally` that runs
even when the client left before the first byte (a generator that never started has no
`finally` to run).

Every cancel names its cause (R21): `client_disconnected` for a client that left,
`sync_deadline` past the bound, `client_cancelled` otherwise. Until D5's migration 0018 the
PostgreSQL store cannot record the first two and refuses them (`param="cause"`); the relay
then cancels with the default cause rather than leave the job running (an interim, marked
where it is). The worker's timings are still pending (no durable carrier across processes,
W3 request 9): `Server-Timing` carries the phase the gateway measures itself.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi.responses import JSONResponse
from starlette.responses import Response

from ...contracts import errors, wire
from ...contracts.limits import DEFAULTS, PilotSettings
from ...contracts.records import (ChunkEventType, ExecutionMode, JobState, NormalizedRequest,
                                  TerminalCause)
from ...observe import metrics
from . import intake
from .catalog import check_capability

log = logging.getLogger("infrx.gateway")

LEGACY, CREDIT = "legacy_usd", "credit"
REGIMES = (LEGACY, CREDIT)
SSE_MEDIA_TYPE = "text/event-stream"
KEEPALIVE = wire.SseFrame(comment="keepalive").render()
PROGRESS_EVENT, ERROR_EVENT = "infrx.progress", "infrx.error"
# Platform deadlines, rendered as the synchronous-deadline class (01: 504).
DEADLINE_CAUSES = frozenset({TerminalCause.queue_wait_expired, TerminalCause.deadline_exceeded,
                             TerminalCause.sync_deadline})


@dataclass(frozen=True)
class _Job:
    """What an answer keeps of an accepted request: scalars only. The request itself (which
    may carry a 96 MiB inline payload) is not kept past acceptance (G1R ingress note)."""

    org_id: str
    handle: str
    request_id: str            # the job's identity: the admission's, also on a replay
    model: str
    created: int
    max_output_tokens: int
    bound: datetime            # the stored deadline plus the grace: past it the gateway cancels


@dataclass
class Relay:
    """One per app. `jobs` is the JobStore (and its CREDIT half), `stream` the journal,
    `media` M's store (`MediaUploads`/`MediaPreparation`), `results` what reads a committed
    result object (`PgJobStore.read_result`), `catalog` the trusted catalog the CREDIT pins
    are rechecked against."""

    jobs: Any
    stream: Any
    media: Any
    regime: str = LEGACY
    catalog: Any = None
    active_rate_card_version: str = ""
    results: Any = None
    limits: PilotSettings = DEFAULTS
    clock: Callable[[], float] = time.time
    sleep: Callable = asyncio.sleep
    registry: Any = None
    # G3: the explicit-async answer. `jobs.register` installs its 202 hook here; None (no jobs
    # router mounted) keeps explicit async refused before anything durable happens.
    on_async: Callable | None = None
    # ponytail: a bounded poll of the store (and of the journal, for SSE). LISTEN/NOTIFY
    # inside D4's append transaction replaces it if per-stream queries ever matter.
    poll_s: float = 0.05
    poll_max_s: float = 1.0
    # The store enforces the deadline on its own clock (R29/R79); the gateway waits this
    # much longer for that to be visible before it cancels itself. Never a skew margin.
    grace_s: float = 5.0
    page: int = 100
    _attaching: dict = field(default_factory=dict)
    _cancels: set = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.regime not in REGIMES:
            raise ValueError(f"accounting regime must be one of {REGIMES}")
        if self.results is None:
            self.results = self.jobs

    # --- acceptance (item 1) ---------------------------------------------------
    async def accept(self, auth, request, idem) -> Response:
        """The ingress's seam: durable acceptance, then the answer that owns the job."""
        try:
            return await self._accept(auth, request, idem)
        except errors.DomainError as refused:
            self._count("infrx_requests_rejected_total", code=refused.code, tenant=auth.org_id)
            raise

    async def _accept(self, auth, request, idem) -> Response:
        if request.execution_mode is ExecutionMode.async_:
            # G3's (POST /v1/jobs, Prefer: respond-async). Without its hook it is refused
            # before anything durable happens, so the relay alone never answers 202.
            if self.on_async is None:
                raise errors.UnsupportedParameter("respond-async is not served on this route",
                                                  param="Prefer")
            return await self.on_async(*await self.admit(auth, request, idem))
        job, admission, headers = await self.admit(auth, request, idem)
        if request.execution_mode is ExecutionMode.stream:
            return _Stream(self, job, headers)
        return _Answer(self, job, headers)

    async def admit(self, auth, request, idem):
        """The durable half of acceptance, the same for every mode (G3's async hook calls it
        too): the R91 lookup, then prepare, stage and one admission by regime, then the
        recheck and the attach. Returns `(job, admission, headers)`; the headers name the
        job (`Inference-Id`, the admission's id, also on a replay) and say whether it is a
        replay. A replay always arrives in the job's own mode: R94 puts the mode in the
        idempotency identity, so a key reused with another mode is `idempotency_conflict`
        (G3's ingress digest)."""
        began = self.clock()
        # R91 (review money-B1): a keyed request that replays a known job is answered from
        # that job before anything is prepared, so a lost answer is recovered by its key
        # even once the customer's media URL has expired, and nothing is fetched again.
        found = await self._lookup(auth.org_id, idem)
        prepared = refs = None
        if found is None:
            # Media is fetched and staged only for a request that maps no job (R91).
            # M2 request 5: preparation replaces the record G1 built, so the staged payload
            # and the admission carry our media refs, never the customer's URL or bytes.
            prepared = await _dependency(self.media.prepare_request(auth.org_id, request))
            refs = await _dependency(self.media.stage(auth.org_id, prepared))
        timings = {"prepare": max(0.0, self.clock() - began)}
        if found is None:
            # The requested name and the idempotency scope exactly as handed (R66, R78).
            admit = self.jobs.admit_credit(prepared, idem) if self.regime == CREDIT \
                else self.jobs.admit(prepared, idem)
            admission = await _dependency(admit)
            if admission.replayed:              # a replay the lookup could not see yet
                found = (admission, (await _dependency(
                    self._owned(admission.org_id, admission.job_handle)))[1])
        else:
            admission = found[0]
        deadline = request.deadline_at if self.regime == CREDIT else admission.deadline_at
        job = _Job(org_id=admission.org_id, handle=admission.job_handle,
                   request_id=admission.request_id, model=request.model_revision,
                   created=int(admission.admitted_at.timestamp()),
                   max_output_tokens=request.max_output_tokens,
                   bound=deadline + timedelta(seconds=self.grace_s))
        headers = {wire.HEADER_INFERENCE_ID: job.request_id,
                   wire.HEADER_SERVER_TIMING: metrics.server_timing(timings)}
        if admission.replayed:
            # Nothing is re-admitted or regenerated: the answer attaches to the same job's
            # wait or stream, and a terminal job answers its committed result - in stream
            # mode from its journal, so past the journal's TTL a stream replay answers
            # `journal_expired` then `[DONE]` (review r2 stream-C2-4; sync recovers it).
            headers[wire.HEADER_IDEMPOTENCY_REPLAYED] = "true"
        if found is None:
            # A fresh admission: the rechecks, then the staged refs bound to the job.
            await self._admitted(job, admission, prepared, refs)
        elif found[1] is None:
            # A job in flight, whose first acceptance may have been cut short between the
            # admission and the attach (money-B2, G3 (b)2).
            await self._resume(job, admission)
        if not admission.replayed:
            self._count("infrx_jobs_accepted_total", mode=request.execution_mode,
                        tenant=auth.org_id)
        if self.registry is not None:
            self.registry.observe_phases(timings)
        return job, admission, headers

    async def _lookup(self, org_id: str, idem):
        """R91: the job a keyed request replays, with its outcome, or None. Until D5 the
        PostgreSQL store cannot look up (`param="lookup"`, refused before any SQL); then
        admission's own replay answer decides, as before (ponytail: interim, D5 lifts it)."""
        if idem.key is None:
            return None
        try:
            found = await _dependency(self.jobs.lookup(org_id, idem))
        except errors.UnsupportedParameter as refused:
            if refused.param != "lookup":
                raise
            return None
        if found is not None and getattr(found[0], "accounting_regime", LEGACY) != self.regime:
            # One key, one regime (the store's own admit rule).
            raise errors.IdempotencyConflict("the key names a job of another accounting regime")
        return found

    async def _resume(self, job: _Job, admission) -> None:
        """The replay of a job in flight (R91; review r2 money-B1/B2). Nothing is prepared or
        staged. Only an acceptance this process provably left unfinished is completed, from
        the record its first acceptance staged (M's staged payload: the prepared request
        and its refs): a job whose payload this process staged and whose refs it never
        bound. M binds and prepares in this process (Limit 8), so that job cannot have been
        prepared, let alone run - its rechecks may still refuse, and cancel, it. Any other
        job in flight (bound, or staged by another process) may be running: it is answered
        as it stands - no recheck, no attach, never a cancel. The bound-gate reads M's
        attach wherever it is recorded (MPILOT: `attached` - this process's, else the durable
        record another process wrote); the staged payload is still this process's."""
        if await _dependency(self.media.attached(job.request_id)) is not None:
            return                              # bound: it may be preparing or running
        try:
            payload = self.media.staged_payload(job.request_id)
        except errors.NotFound:
            return                              # staged by another process: as it stands
        data = await _dependency(self.media.objects.get(payload.ref))
        if data is None:
            return
        staged = NormalizedRequest.model_validate_json(data)
        await self._admitted(job, admission, staged, staged.media)

    async def _admitted(self, job: _Job, admission, prepared, refs) -> None:
        """Complete an acceptance: the pinned card and capability rechecks (CREDIT), then the
        staged refs bound to the job - before the refs, so a refused job can never run. Run
        on a fresh admission and, from the first acceptance's staged record, on the replay
        of a job still in flight (`_resume`); both steps are idempotent. A definitive
        refusal cancels the job (nothing ran, nothing is billed) and is answered; a
        dependency that failed leaves the job for the same-key retry its 503 invites
        (money-B2; the stored deadline ends it otherwise)."""
        try:
            if self.regime == CREDIT:
                pins = admission.pins
                # Wire-in request "G1R (active card)": the card admission pinned must be the
                # one this deployment approved to serve (R69: otherwise it is unpriced here).
                if pins.rate_card_version != self.active_rate_card_version:
                    raise errors.InvalidRequest("the model is not priced for this deployment")
                # G1R Limit 2: the ingress checked capability against the revision it
                # resolved; an alias that moved since must still serve what was asked.
                serving = await _dependency(self.catalog.serving_revision(
                    pins.serving_version_id))
                if serving is None:
                    raise errors.NotFound("the pinned serving revision is not in the catalog")
                check_capability(serving, prepared.messages, prepared.execution_mode)
            self._attaching[job.request_id] = job.org_id
            try:
                await _dependency(self.media.attach(job.request_id, refs))
            finally:
                self._attaching.pop(job.request_id, None)
        except errors.DependencyUnavailable:
            raise
        except BaseException as refused:
            ended = await self.cancel(job.org_id, job.handle, quiet=True)
            if ended is None and isinstance(refused, errors.DomainError):
                # money-N1: the job is not cancelled yet, so the refusal is not final; the
                # retry this 503 invites cancels it and answers the refusal.
                raise errors.DependencyUnavailable("the refused job is not cancelled yet") \
                    from None
            raise

    def job_org(self, job_id: str) -> str:
        """M's `job_org` for `attach`: the organization of a job this relay is attaching,
        from the store's own admission record (R55) - never a caller's word."""
        org_id = self._attaching.get(job_id)
        if org_id is None:
            raise errors.NotFound(f"no job {job_id}")
        return org_id

    # --- cancellation (item 4) -------------------------------------------------
    async def cancel(self, org_id: str, handle: str, *,
                     cause: TerminalCause = TerminalCause.client_cancelled, quiet: bool = False):
        """Durable cancellation with its R21 cause, shielded so the caller's own cancellation
        cannot abort it, answering the COMMITTED outcome (D3 request 2): a job whose
        completion won returns that outcome; another tenant's handle is `not_found` like an
        unknown one. `quiet` turns an unanswerable cancel into None (the store's own
        deadline, R29, then terminalizes the job), for paths that must not raise over another
        error."""
        task = asyncio.ensure_future(self._cancel(org_id, handle, cause))
        self._cancels.add(task)                 # a strong reference until it is done
        task.add_done_callback(self._cancels.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not quiet:
                raise
            log.exception("cancel of job %s failed; its stored deadline still ends it", handle)
            return None

    async def drain(self, timeout_s: float) -> None:
        """Wait, bounded, for the shielded cancels still in flight: a process must not exit
        with a durable cancel half done (review stream-S1; `pilot.lifespan` on shutdown)."""
        if self._cancels:
            await asyncio.wait(set(self._cancels), timeout=timeout_s)

    async def _cancel(self, org_id: str, handle: str, cause: TerminalCause):
        try:
            return await self.jobs.cancel(org_id, handle, cause=cause)
        except errors.UnsupportedParameter as refused:
            if refused.param != "cause" or cause is TerminalCause.client_cancelled:
                raise
            # ponytail: interim until D5's migration 0018 - the PostgreSQL store cannot record
            # this cause yet and refuses it before any SQL. The job is cancelled with the
            # default cause rather than left running for want of the right label (the client
            # never sees the refusal). D5 lifts it; nothing here changes then.
            log.warning("cancel cause %s is not recordable yet: cancelling job %s as %s",
                        cause.value, handle, TerminalCause.client_cancelled.value)
            return await self._cancel(org_id, handle, TerminalCause.client_cancelled)
        except errors.AlreadyTerminal:
            # D3 handback: after `already_terminal`, the outcome is read, never assumed.
            return (await self._owned(org_id, handle))[1]

    async def _owned(self, org_id: str, handle: str):
        if self.regime == CREDIT:
            return await self.jobs.get_owned_credit(org_id, handle)
        return await self.jobs.get_owned(org_id, handle)

    async def _outcome(self, job: _Job):
        """The committed outcome, None while the job runs. A read that failed for want of
        the database is retried at the next poll, not reported as the job's end."""
        try:
            return (await self._owned(job.org_id, job.handle))[1]
        except errors.DomainError:
            raise
        except Exception:
            log.warning("status read of job %s failed; retrying", job.handle, exc_info=True)
            return None

    async def _at_bound(self, job: _Job):
        """Past the bound: cancel, and answer what is committed. A completion that won is
        the answer (01: durable state wins); our own cancel is the synchronous deadline."""
        outcome = await self.cancel(job.org_id, job.handle, cause=TerminalCause.sync_deadline,
                                    quiet=True)
        if outcome is None:                     # the cancel is unconfirmed: claim no state
            raise errors.DeadlineExceeded("the job passed its deadline")
        if outcome.state is JobState.cancelled:
            raise errors.DeadlineExceeded("the job passed its deadline and was cancelled",
                                          infrx={"state": JobState.cancelled.value})
        return outcome

    # --- the synchronous wait (item 2) ----------------------------------------
    async def wait(self, job: _Job, gone: asyncio.Task):
        """The committed outcome, or None when the client left (the job is then cancelled).

        Any other way out - an error, or this task being cancelled - cancels too: a sync
        caller got no identity to come back with, so a job left running would be an orphan.
        """
        delay = self.poll_s
        try:
            while not gone.done() and self._now() < job.bound:
                outcome = await self._outcome(job)
                if outcome is not None:
                    return outcome
                await self.sleep(delay)
                delay = min(2 * delay, self.poll_max_s)
        except BaseException:
            await self.cancel(job.org_id, job.handle, quiet=True)
            raise
        if gone.done():
            await self.cancel(job.org_id, job.handle, cause=TerminalCause.client_disconnected,
                              quiet=True)
            return None
        return await self._at_bound(job)

    async def answer(self, job: _Job, outcome) -> Response:
        """200 with the committed result, or the envelope the outcome maps to."""
        if outcome.state is not JobState.succeeded:
            raise _refusal(outcome, stream=False)
        # G7 (RESULT-EXPIRY): a sync answer - a replay by key above all - is a read of the
        # result like any other: only before its PERSISTED expiry, on the store clock, and
        # never for a success with none persisted (F2C.b; never recomputed from settings).
        expires = outcome.result_expires_at
        if expires is None or await _dependency(self.jobs.db_now()) >= expires:
            raise errors.ResultExpired("the result passed its retention")
        text = await self.results.read_result(job.org_id, outcome.result_ref)
        fields = dict(id=f"chatcmpl-{job.request_id}", created=job.created, model=job.model,
                      choices=(wire.ChatChoice(index=0, message=wire.ChatMessage(
                          role="assistant", content=text),
                          finish_reason=_finish_reason(job, outcome)),))
        if outcome.usage is None:
            # A committed success whose usage is unknown (held_unknown, money-N2): the
            # result, no counts - `usage` is left out rather than invented.
            body = wire.ChatCompletionResponse.model_construct(**fields, usage=None)
        else:
            body = wire.ChatCompletionResponse(**fields, usage=wire.ChatUsage.of(outcome.usage))
        return JSONResponse(body.model_dump(mode="json", exclude_none=True))

    # --- the SSE relay (item 3) -------------------------------------------------
    async def pump(self, job: _Job, emit, gone: asyncio.Task, *, cursor=None,
                   cancel_on_gone: bool = True) -> None:
        """Relay the committed journal until a terminal commit is known, then end on it.

        G3: `cursor` resumes after a client's `Last-Event-ID`; an events observer passes
        `cancel_on_gone=False`, because an observer that leaves detaches - it never cancels."""
        ending, first = None, True
        delay, quiet_since = self.poll_s, self._now()
        while True:
            if gone.done() and not cancel_on_gone:
                return
            if gone.done():
                await self.cancel(job.org_id, job.handle,
                                  cause=TerminalCause.client_disconnected, quiet=True)
                return
            try:
                chunks, cursor = await self.stream.read_owned(job.org_id, job.handle, cursor,
                                                              self.page)
            except errors.DomainError:
                raise
            except Exception:
                # The database, not the job: retried at the next poll, never taken for an
                # empty journal (which could end the stream) nor for the end of the job.
                log.warning("journal read of job %s failed; retrying", job.handle,
                            exc_info=True)
                chunks = None
            for chunk in chunks or ():
                if chunk.event_type is ChunkEventType.terminal:
                    # R30: written by the settling transaction from the stored outcome.
                    outcome = (await self._owned(job.org_id, job.handle))[1]
                    return await self._finish(job, emit, outcome, chunk.cursor.token)
                frame = _frame(job, chunk, first)
                if frame is not None:
                    await emit(frame)
                    quiet_since = self._now()
                    first = first and chunk.event_type is not ChunkEventType.delta
            if chunks:
                delay = self.poll_s
                continue
            if chunks is not None and ending is not None:
                # D3's terminalizations write no terminal event until D4's trigger lands;
                # the committed outcome ends the stream once the journal holds nothing more.
                return await self._finish(job, emit, ending, None)
            if ending is None:
                ending = await self._outcome(job)
            if ending is None and self._now() >= job.bound:
                ending = await self._at_bound(job)
            if ending is not None and chunks is not None:
                continue                    # drain what was committed before it, then end
            if (self._now() - quiet_since).total_seconds() >= self.limits.sse_keepalive_s:
                await emit(KEEPALIVE)
                quiet_since = self._now()
            await self.sleep(delay)
            delay = min(2 * delay, self.poll_max_s)

    async def _finish(self, job: _Job, emit, outcome, token: str | None) -> None:
        if outcome.state is JobState.succeeded:
            if outcome.usage is not None:
                await emit(wire.SseFrame(data=_chunk(job, (), usage=outcome.usage)).render())
        else:
            await emit(_error_frame(job, _refusal(outcome, stream=True)))
        await emit(wire.SseFrame(id=token, data=wire.DONE).render())

    # --- helpers ----------------------------------------------------------------
    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def _count(self, name: str, **labels) -> None:
        if self.registry is not None:           # I3B request 3: none injected, none counted
            self.registry.inc(name, **labels)


async def _dependency(awaitable):
    """A store or media call at acceptance. Typed refusals pass through (the route guard
    renders them); anything else is a dependency that failed - a typed, retryable 503,
    with the driver's text kept in the log (it may carry a DSN or a host)."""
    try:
        return await awaitable
    except errors.DomainError:
        raise
    except Exception:
        log.exception("a durable dependency failed at acceptance")
        raise errors.DependencyUnavailable("a durable dependency failed at acceptance") from None


def _refusal(outcome, *, stream: bool) -> errors.DomainError:
    """A terminal outcome that is not a success, as the contract's error. Keyed on the
    committed `state` and `cause`: a cancel after publication (`held_unknown`, debit 0)
    is cancelled, never failed and never billed; one the sync deadline caused is that
    deadline."""
    detail = {"state": outcome.state.value}
    if outcome.state is JobState.expired or outcome.cause in DEADLINE_CAUSES:
        return errors.DeadlineExceeded("the job passed its deadline", infrx=detail)
    if outcome.state is JobState.cancelled:
        return errors.StateConflict("the job was cancelled", infrx=detail)
    if outcome.cause is TerminalCause.invalid_media:
        return errors.UnsupportedMedia("the media could not be prepared", infrx=detail)
    failed = errors.StreamInterrupted if stream else errors.InternalError
    return failed("the job failed", infrx=detail)


def _finish_reason(job: _Job, outcome) -> str:
    if outcome.usage is None:
        return "stop"
    return "length" if outcome.usage.completion_tokens >= job.max_output_tokens else "stop"


def _chunk(job: _Job, choices, *, usage=None) -> dict:
    body = wire.ChatCompletionChunk(id=f"chatcmpl-{job.request_id}", created=job.created,
                                    model=job.model, choices=choices,
                                    usage=wire.ChatUsage.of(usage) if usage else None)
    return body.model_dump(mode="json", exclude_none=True)


def _identity(job: _Job, phase: str) -> dict:
    return {"job_handle": job.handle, "request_id": job.request_id, "phase": phase}


def _frame(job: _Job, chunk, first: bool) -> str | None:
    """One committed journal chunk as the customer sees it, or None for what carries
    nothing of theirs: the usage event (the counts come with the committed outcome) and an
    engine error event (the terminal outcome is what is reported)."""
    token = chunk.cursor.token
    if chunk.event_type is ChunkEventType.progress:
        phase = chunk.payload.get("phase")
        return wire.SseFrame(id=token, event=PROGRESS_EVENT,
                             data=_identity(job, phase if isinstance(phase, str) else "running"),
                             event_type=chunk.event_type).render()
    if chunk.event_type is ChunkEventType.delta:
        # R58/R80: `visible` only. `raw` (and its `content` alias) carries the reasoning
        # block; a delta without `visible` is relayed as nothing rather than as raw.
        visible = chunk.payload.get("visible")
        if not isinstance(visible, str):
            return None
        delta = {"role": "assistant", "content": visible} if first else {"content": visible}
        return wire.SseFrame(id=token, data=_chunk(job, (wire.ChatChunkChoice(index=0,
                                                                              delta=delta),)),
                             event_type=chunk.event_type).render()
    return None


def _error_frame(job: _Job, error: errors.DomainError) -> str:
    body = errors.envelope(error, job.request_id).model_dump(mode="json", exclude_none=True)
    return wire.SseFrame(event=ERROR_EVENT, data=body).render()


def _watch(receive) -> asyncio.Task:
    """Done once the client has gone (`http.disconnect`, or a receive that fails)."""
    async def watch() -> None:
        try:
            while (await receive())["type"] != "http.disconnect":
                pass
        except Exception:
            pass
    return asyncio.ensure_future(watch())


class _Owned(Response):
    """An ASGI response that owns one accepted job. `headers` are set now, so the ingress
    can still add `Inference-Id`; nothing is sent before `__call__`."""

    def __init__(self, relay: Relay, job: _Job, headers: dict[str, str]) -> None:
        self.relay, self.job = relay, job
        self.status_code = 200
        self.background = None
        self.init_headers(headers)


class _Answer(_Owned):
    """Sync: nothing is sent until the terminal commit (or the envelope it maps to)."""

    async def __call__(self, scope, receive, send) -> None:
        gone = _watch(receive)
        try:
            outcome = await self.relay.wait(self.job, gone)
            if outcome is None:
                return                          # the client left; the job is cancelled
            answer = await self.relay.answer(self.job, outcome)
        except errors.DomainError as error:
            answer = intake.response(error, self.job.request_id)
        except Exception:
            log.exception("the synchronous answer for job %s failed", self.job.handle)
            answer = intake.response(errors.InternalError(), self.job.request_id)
        finally:
            gone.cancel()
        for name, value in self.headers.items():
            answer.headers.setdefault(name, value)
        await answer(scope, receive, send)


class _Stream(_Owned):
    """SSE from the committed journal. Errors after the headers are an `infrx.error` frame,
    never a status: the response already started with 200."""

    media_type = SSE_MEDIA_TYPE

    def __init__(self, relay: Relay, job: _Job, headers: dict[str, str]) -> None:
        super().__init__(relay, job, {**headers, "cache-control": "no-cache"})

    async def __call__(self, scope, receive, send) -> None:
        relay, job = self.relay, self.job
        gone = _watch(receive)
        named = False

        async def emit(text: str) -> None:
            await send({"type": "http.response.body", "body": text.encode(), "more_body": True})

        try:
            await send({"type": "http.response.start", "status": 200,
                        "headers": self.raw_headers})
            await emit(wire.SseFrame(event=PROGRESS_EVENT,
                                     data=_identity(job, "accepted")).render())
            named = True
            await relay.pump(job, emit, gone)
        except asyncio.CancelledError:
            # No identity reached the client (an orphan), or the client had already left
            # (review stream-S7); otherwise the job is left to its worker.
            if not named or gone.done():
                await relay.cancel(job.org_id, job.handle, quiet=True, cause=(
                    TerminalCause.client_disconnected if gone.done()
                    else TerminalCause.client_cancelled))
            raise
        except Exception as failure:
            # A replay gap, an expired journal, the store gone, or the client gone (a send
            # that fails): the relay cannot go on, so the job must not go on for nobody.
            left = gone.done() or isinstance(failure, OSError)       # a send that failed
            ended = await relay.cancel(job.org_id, job.handle, quiet=True, cause=(
                TerminalCause.client_disconnected if left else TerminalCause.client_cancelled))
            if left:
                return
            error = failure if isinstance(failure, errors.DomainError) \
                else errors.StatusUnknown("the stream could not continue")
            try:
                await emit(_error_frame(job, error))
                if ended is not None:
                    await emit(wire.SseFrame(data=wire.DONE).render())
            except Exception:
                return
        finally:
            gone.cancel()
        try:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception:
            pass
