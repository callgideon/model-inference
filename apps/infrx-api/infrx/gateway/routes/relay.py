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

Two things are pending on other tasks and named where they bite: the cancel cause (the
port records `client_cancelled` for every cancel; `client_disconnected`/`sync_deadline` need
D's `cancel(..., cause=)`, R21) and the worker's timings (no durable carrier across
processes, W3 request 9) - `Server-Timing` carries the phase the gateway measures itself.
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
from ...contracts.records import ChunkEventType, ExecutionMode, JobState, TerminalCause
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
            # G3's (POST /v1/jobs, Prefer: respond-async). Refused before anything durable
            # happens, and this route never answers 202.
            raise errors.UnsupportedParameter("respond-async is not served on this route",
                                              param="Prefer")
        began = self.clock()
        # M2 request 5: preparation replaces the record G1 built, so the staged payload and
        # the admission carry our media refs, never the customer's URL or inline bytes.
        prepared = await _dependency(self.media.prepare_request(auth.org_id, request))
        refs = await _dependency(self.media.stage(auth.org_id, prepared))
        timings = {"prepare": max(0.0, self.clock() - began)}
        # The requested name and the idempotency scope exactly as handed (R66, R78).
        admit = self.jobs.admit_credit(prepared, idem) if self.regime == CREDIT \
            else self.jobs.admit(prepared, idem)
        admission = await _dependency(admit)
        deadline = request.deadline_at if self.regime == CREDIT else admission.deadline_at
        job = _Job(org_id=admission.org_id, handle=admission.job_handle,
                   request_id=admission.request_id, model=request.model_revision,
                   created=int(admission.admitted_at.timestamp()),
                   max_output_tokens=request.max_output_tokens,
                   bound=deadline + timedelta(seconds=self.grace_s))
        headers = {wire.HEADER_INFERENCE_ID: job.request_id,
                   wire.HEADER_SERVER_TIMING: metrics.server_timing(timings)}
        if admission.replayed:
            # Nothing is re-admitted, re-attached or regenerated: the answer attaches to the
            # same job's wait or stream, and a terminal job answers its committed result.
            headers[wire.HEADER_IDEMPOTENCY_REPLAYED] = "true"
        else:
            await self._admitted(job, admission, prepared, refs)
            self._count("infrx_jobs_accepted_total", mode=request.execution_mode,
                        tenant=auth.org_id)
        if self.registry is not None:
            self.registry.observe_phases(timings)
        if request.execution_mode is ExecutionMode.stream:
            return _Stream(self, job, headers)
        return _Answer(self, job, headers)

    async def _admitted(self, job: _Job, admission, prepared, refs) -> None:
        """What a fresh admission still has to pass, then the staged refs bound to it. A
        refusal here cancels the job it just admitted (nothing ran, nothing is billed)."""
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
        except BaseException:
            await self.cancel(job.org_id, job.handle, quiet=True)
            raise

    def job_org(self, job_id: str) -> str:
        """M's `job_org` for `attach`: the organization of a job this relay is attaching,
        from the store's own admission record (R55) - never a caller's word."""
        org_id = self._attaching.get(job_id)
        if org_id is None:
            raise errors.NotFound(f"no job {job_id}")
        return org_id

    # --- cancellation (item 4) -------------------------------------------------
    async def cancel(self, org_id: str, handle: str, *, quiet: bool = False):
        """Durable cancellation, shielded so the caller's own cancellation cannot abort it,
        answering the COMMITTED outcome (D3 request 2): a job whose completion won returns
        that outcome; another tenant's handle is `not_found` like an unknown one. `quiet`
        turns an unanswerable cancel into None (the store's own deadline, R29, then
        terminalizes the job), for the paths that must not raise over another error."""
        task = asyncio.ensure_future(self._cancel(org_id, handle))
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

    async def _cancel(self, org_id: str, handle: str):
        # ponytail: the port records `client_cancelled` for every cancel; a disconnect and a
        # sync deadline are `client_disconnected`/`sync_deadline` under R21 once D's
        # `cancel(org, handle, *, cause)` exists (request D-new). Not worked around here.
        try:
            return await self.jobs.cancel(org_id, handle)
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
        outcome = await self.cancel(job.org_id, job.handle, quiet=True)
        if outcome is None or outcome.state is JobState.cancelled:
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
            await self.cancel(job.org_id, job.handle, quiet=True)
            return None
        return await self._at_bound(job)

    async def answer(self, job: _Job, outcome) -> Response:
        """200 with the committed result, or the envelope the outcome maps to."""
        if outcome.state is not JobState.succeeded:
            raise _refusal(outcome, stream=False)
        text = await self.results.read_result(job.org_id, outcome.result_ref)
        body = wire.ChatCompletionResponse(
            id=f"chatcmpl-{job.request_id}", created=job.created, model=job.model,
            choices=(wire.ChatChoice(index=0, message=wire.ChatMessage(role="assistant",
                                                                       content=text),
                                     finish_reason=_finish_reason(job, outcome)),),
            usage=wire.ChatUsage.of(outcome.usage))
        return JSONResponse(body.model_dump(mode="json", exclude_none=True))

    # --- the SSE relay (item 3) -------------------------------------------------
    async def pump(self, job: _Job, emit, gone: asyncio.Task) -> None:
        """Relay the committed journal until a terminal commit is known, then end on it."""
        cursor, ending, first = None, None, True
        delay, quiet_since = self.poll_s, self._now()
        while True:
            if gone.done():
                await self.cancel(job.org_id, job.handle, quiet=True)
                return
            chunks, cursor = await self.stream.read_owned(job.org_id, job.handle, cursor,
                                                          self.page)
            for chunk in chunks:
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
            if ending is not None:
                # D3's terminalizations write no terminal event until D4's trigger lands;
                # the committed outcome ends the stream once the journal holds nothing more.
                return await self._finish(job, emit, ending, None)
            ending = await self._outcome(job)
            if ending is None and self._now() >= job.bound:
                ending = await self._at_bound(job)
            if ending is not None:
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
    is cancelled, never failed and never billed."""
    detail = {"state": outcome.state.value}
    if outcome.state is JobState.cancelled:
        return errors.StateConflict("the job was cancelled", infrx=detail)
    if outcome.state is JobState.expired or outcome.cause in DEADLINE_CAUSES:
        return errors.DeadlineExceeded("the job passed its deadline", infrx=detail)
    if outcome.cause is TerminalCause.invalid_media:
        return errors.UnsupportedMedia("the media could not be prepared", infrx=detail)
    failed = errors.StreamInterrupted if stream else errors.InternalError
    return failed("the job failed", infrx=detail)


def _finish_reason(job: _Job, outcome) -> str:
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
            if not named:                       # no identity reached the client: an orphan
                await relay.cancel(job.org_id, job.handle, quiet=True)
            raise
        except Exception as failure:
            # A replay gap, an expired journal, the store gone, or the client gone (a send
            # that fails): the relay cannot go on, so the job must not go on for nobody.
            ended = await relay.cancel(job.org_id, job.handle, quiet=True)
            if gone.done() or isinstance(failure, OSError):
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
