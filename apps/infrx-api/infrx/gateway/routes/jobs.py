"""G3: explicit asynchronous jobs - the 202, status, result, event replay and DELETE.

    rt.relay, rt.ingress = <G2's Relay>, <its IngressDeps>     # pilot.build_ingress_deps
    jobs.register(app, rt)                     # after ingress.register (its error handlers)

Five routes, each answering only for the authenticated organization's own jobs:

* `POST /v1/jobs`, and `Prefer: respond-async` on `/v1/chat/completions`: the chat body
  through the ingress's own intake, admitted by `Relay.admit` like any request, then
  answered by `Jobs.accepted` (installed as `Relay.on_async`). The **202** `JobAccepted` is
  built only once the admission has committed and the staged refs are attached. Nothing
  watches the connection afterwards: a client that leaves holds a handle, or retries with its
  key, and the job runs on. A replay by key answers the original acceptance as it stands now.
* `GET /v1/jobs/{handle}`: `JobStatus` from the committed row and outcome.
* `GET /v1/jobs/{handle}/result`: the committed result (`JobResult`); `result_pending` while
  the job runs; a failure is a result with no `response`, not an error; `result_expired`
  past the result's TTL.
* `GET /v1/jobs/{handle}/events`: the committed journal as SSE from `Last-Event-ID`, through
  `Relay.pump`. An observer that leaves detaches; it never cancels.
* `DELETE /v1/jobs/{handle}`: `Relay.cancel(cause=client_cancelled)`, shielded, answering
  the committed outcome.

Every handle route authenticates, then checks the audience and the handle's grammar before
any store read, then asks the store with the key's organization (R59/R66). A malformed, an
unknown and another tenant's handle are one 404. Expiry is judged on the store clock
(`db_now`, R29/R79), never the gateway's.
"""
from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from ...contracts import errors, ids, wire
from ...contracts.records import (Budgets, Cursor, ExecutionMode, JobState, SettlementState,
                                  TerminalCause, UsageCertainty)
from ...contracts.v2.records import CredentialAudience
from . import intake
from .ingress import Ingress
from .relay import (CREDIT, PROGRESS_EVENT, SSE_MEDIA_TYPE, _dependency, _error_frame,
                    _identity, _Job, _watch)

log = logging.getLogger("infrx.gateway")

JOBS_PATH = "/v1/jobs"
JOB_PATH = JOBS_PATH + "/{handle}"
RESULT_PATH = JOB_PATH + "/result"
EVENTS_PATH = JOB_PATH + "/events"
HEADER_LOCATION = "Location"
# ponytail: one fixed poll hint (`Retry-After` on the 202) for every accepted job; no `limits`
# setting names a poll interval. A hint per phase if clients poll too hard.
POLL_AFTER_S = 2
# The audiences that own jobs. An operator credential runs no inference (R66), so owns none.
OWNERS = frozenset({CredentialAudience.consumer, CredentialAudience.provider_dev})
# An observer has no deadline of its own: the store's ends the job (R29). The relay's bound,
# past which it cancels a sync or stream caller's job, is never reached by an observer.
NEVER = datetime.max.replace(tzinfo=timezone.utc)


class Jobs:
    """One per app: the relay it answers for and the ingress intake it validates with."""

    def __init__(self, rt, relay, deps) -> None:
        self.relay = relay
        # The chat intake verbatim (no second parser): the same resolver over `rt.auth`'s key
        # caches, the same bounds and validator, `deps.large_bodies` shared.
        self.ingress = Ingress(rt, deps)

    async def accepted(self, job: _Job, admission, headers: dict) -> JSONResponse:
        """`Relay.on_async`: the 202. `Relay.admit` has returned, so the admission is committed
        and a fresh job's staged refs are attached. Nothing after this watches the client."""
        replayed, outcome = admission.replayed, None
        if replayed:
            # The original acceptance as it stands now (job_idempotent_replay.json). A store
            # that fails this read is a retryable 503 and the job is left for the retry.
            admission, outcome = await _dependency(self.relay._owned(job.org_id, job.handle))
        body = wire.JobAccepted(job_handle=job.handle, request_id=job.request_id,
                                state=self.state_of(admission, outcome),
                                execution_mode=ExecutionMode.async_,
                                created_at=admission.admitted_at,
                                deadline_at=self.deadline_of(job, admission),
                                idempotency_replayed=replayed)
        return JSONResponse(body.model_dump(mode="json"), status_code=202, headers={
            **headers, wire.HEADER_PREFERENCE_APPLIED: wire.PREFER_RESPOND_ASYNC,
            HEADER_LOCATION: f"{JOBS_PATH}/{job.handle}",
            wire.HEADER_RETRY_AFTER: str(POLL_AFTER_S)})

    async def owner(self, request: Request) -> tuple[str, str]:
        """The caller's organization and a well-formed handle, before any store read. A key
        that owns no job and a malformed handle are the 404 an unknown or foreign handle gets
        from the store."""
        auth = await self.ingress.auth.context(request)
        handle = request.path_params["handle"]
        if auth.audience not in OWNERS or not ids.JOB_HANDLE_RE.fullmatch(handle):
            raise errors.NotFound("no job with that handle")
        return auth.org_id, handle

    # --- rendering the committed row --------------------------------------------------
    def state_of(self, admission, outcome) -> JobState:
        if outcome is not None:
            return outcome.state
        if self.relay.regime == CREDIT:
            # ponytail: `AdmissionV2` carries no lifecycle state and the port refuses a v1
            # read of a CREDIT job, so a CREDIT job not yet terminal reads as admitted
            # (`preparing`, as of `updated_at` = `created_at`). F/D integration request.
            return JobState.preparing
        return admission.state

    def deadline_of(self, job: _Job, admission) -> datetime:
        """The stored deadline. For CREDIT, `AdmissionV2` carries none (01a §1 keeps it on the
        v1 row), so it is the store's own rule (R-3): the earlier of the caller's bound
        (G2's `request.deadline_at`, inside `job.bound`) and `admitted_at` plus the budgets."""
        if self.relay.regime != CREDIT:
            return admission.deadline_at
        # ponytail: recomputed until the CREDIT read answers the stored deadline (F/D).
        budgets = Budgets.of(self.relay.limits, ExecutionMode.async_)
        return min(job.bound - timedelta(seconds=self.relay.grace_s),
                   admission.admitted_at + timedelta(seconds=budgets.preparation_s
                                                     + budgets.queue_wait_s
                                                     + budgets.generation_s))

    def model_of(self, admission) -> str:
        """The model name the caller asked for, as admission recorded it (R86)."""
        if self.relay.regime == CREDIT:
            return admission.pins.requested_model
        return admission.price_snapshot.model_revision

    def result_expiry(self, outcome) -> datetime | None:
        """When a committed success's result stops being served: `result_ttl_s` after its
        settlement, both instants on the store clock. None for a job that has no chat result:
        not terminal, not a success, or a success without authoritative usage (the chat shape
        requires usage, and none is invented).

        ponytail: the TTL is the route's (`limits.result_ttl_s` over `settled_at`) until D5
        stores `jobs.result_expires_at`; then read the store's."""
        if (outcome is None or outcome.state is not JobState.succeeded
                or outcome.usage is None or not outcome.result_ref):
            return None
        return outcome.settled_at + timedelta(seconds=self.relay.limits.result_ttl_s)

    def status_of(self, admission, outcome, now: datetime) -> wire.JobStatus:
        expires = self.result_expiry(outcome)
        available = expires is not None and now < expires
        usage = outcome.usage if outcome is not None else None
        held = outcome is not None and outcome.settlement_state is SettlementState.held_unknown
        certainty = (UsageCertainty.authoritative if usage is not None
                     else UsageCertainty.unknown if held else None)
        return wire.JobStatus(
            job_handle=admission.job_handle, request_id=admission.request_id,
            state=self.state_of(admission, outcome),
            cause=outcome.cause if outcome is not None else None,
            created_at=admission.admitted_at,
            updated_at=outcome.settled_at if outcome is not None else admission.admitted_at,
            result_available=available, result_expires_at=expires if available else None,
            usage=wire.ChatUsage.of(usage) if usage is not None else None,
            usage_certainty=certainty.value if certainty is not None else None)

    def response_of(self, admission, outcome, text: str) -> wire.ChatCompletionResponse:
        """`chat_success_nonstream.json`'s shape, without `finish_reason`: the admitted output
        ceiling it compares with is not on the owned read (integration request), and a guess
        is not a reason."""
        return wire.ChatCompletionResponse(
            id=ids.chat_completion_id(admission.request_id),
            created=int(admission.admitted_at.timestamp()), model=self.model_of(admission),
            choices=(wire.ChatChoice(index=0, message=wire.ChatMessage(role="assistant",
                                                                       content=text)),),
            usage=wire.ChatUsage.of(outcome.usage))

    def job_of(self, admission) -> _Job:
        """What the relay's pump needs of a job this process did not accept. The output
        ceiling is not read by the pump (only by the sync answer's finish reason)."""
        return _Job(org_id=admission.org_id, handle=admission.job_handle,
                    request_id=admission.request_id, model=self.model_of(admission),
                    created=int(admission.admitted_at.timestamp()), max_output_tokens=0,
                    bound=NEVER)


def _answer(body, admission) -> JSONResponse:
    """A job resource. `Inference-Id` names the job's inference (01), not this read."""
    return JSONResponse(body.model_dump(mode="json", exclude_none=True),
                        headers={wire.HEADER_INFERENCE_ID: admission.request_id})


def _as_async(request: Request) -> Request:
    """The request as the chat validator sees it on `POST /v1/jobs`: `Prefer: respond-async`
    in place of whatever the client sent (`validate.execution_mode` reads the mode there)."""
    headers = [(name, value) for name, value in request.scope["headers"] if name != b"prefer"]
    headers.append((b"prefer", wire.PREFER_RESPOND_ASYNC.encode()))
    return Request({**request.scope, "headers": headers}, request.receive)


class _Events(Response):
    """SSE from the committed journal for an observer: the identity frame, then the relay's
    pump from the cursor. After the headers an error is an `infrx.error` frame. It never
    cancels: an observer that leaves, fails or is stopped leaves the job to its worker
    (01: an async event-observer disconnect detaches only)."""

    media_type = SSE_MEDIA_TYPE

    def __init__(self, relay, job: _Job, cursor, state: JobState) -> None:
        self.relay, self.job, self.cursor, self.state = relay, job, cursor, state
        self.status_code = 200
        self.background = None
        self.init_headers({wire.HEADER_INFERENCE_ID: job.request_id, "cache-control": "no-cache"})

    async def __call__(self, scope, receive, send) -> None:
        gone = _watch(receive)

        async def emit(text: str) -> None:
            await send({"type": "http.response.body", "body": text.encode(), "more_body": True})

        try:
            await send({"type": "http.response.start", "status": 200,
                        "headers": self.raw_headers})
            await emit(wire.SseFrame(event=PROGRESS_EVENT,
                                     data=_identity(self.job, self.state.value)).render())
            await self.relay.pump(self.job, emit, gone, cursor=self.cursor,
                                  cancel_on_gone=False)
        except Exception as failure:
            # A gap or an expiry mid-replay, the store gone, or the client gone (a failed send).
            if not isinstance(failure, errors.DomainError):
                log.warning("events of job %s ended early", self.job.handle, exc_info=True)
                failure = errors.StatusUnknown("the event stream could not continue")
            with contextlib.suppress(Exception):
                await emit(_error_frame(self.job, failure))
        finally:
            gone.cancel()
        with contextlib.suppress(Exception):
            await send({"type": "http.response.body", "body": b"", "more_body": False})


def register(app, rt):
    """Mount the jobs routes over `rt.relay` and `rt.ingress` (r1 R44) and install the 202
    hook. Without a relay nothing is mounted and None is returned (M-FAILCLOSED: no fake
    fallback); without `rt.ingress` the ingress intake refuses to start."""
    relay = getattr(rt, "relay", None)
    if relay is None:
        return None
    jobs = Jobs(rt, relay, getattr(rt, "ingress", None))
    guarded = intake.guard(jobs.ingress.deps.new_request_id)

    @app.post(JOBS_PATH)
    @guarded
    async def create_job(request: Request, request_id: str):
        """The chat body, always async. A `Prefer` header here is ignored - the route is the
        preference - so none is reported applied."""
        auth, normalized, idem = await jobs.ingress.validated(_as_async(request), request_id)
        answer = await relay.accept(auth, normalized, idem)
        del answer.headers[wire.HEADER_PREFERENCE_APPLIED]
        answer.headers.setdefault(wire.HEADER_INFERENCE_ID, request_id)
        return answer

    @app.get(JOB_PATH)
    @guarded
    async def job_status(request: Request, request_id: str):
        org, handle = await jobs.owner(request)
        admission, outcome = await relay._owned(org, handle)
        return _answer(jobs.status_of(admission, outcome, await relay.jobs.db_now()), admission)

    @app.get(RESULT_PATH)
    @guarded
    async def job_result(request: Request, request_id: str):
        org, handle = await jobs.owner(request)
        admission, outcome = await relay._owned(org, handle)
        if outcome is None:
            raise errors.ResultPending("the job is not terminal")
        expires, response = jobs.result_expiry(outcome), None
        if expires is not None:
            if await relay.jobs.db_now() >= expires:
                raise errors.ResultExpired("the result passed its retention")
            text = await relay.results.read_result(org, outcome.result_ref)
            response = jobs.response_of(admission, outcome, text)
        return _answer(wire.JobResult(
            job_handle=admission.job_handle, request_id=admission.request_id,
            state=outcome.state, cause=outcome.cause, response=response,
            usage=wire.ChatUsage.of(outcome.usage) if outcome.usage is not None else None,
            completed_at=outcome.settled_at), admission)

    @app.get(EVENTS_PATH)
    @guarded
    async def job_events(request: Request, request_id: str):
        org, handle = await jobs.owner(request)
        last = request.headers.get(wire.HEADER_LAST_EVENT_ID)
        cursor = Cursor.parse(last) if last is not None else None        # 400 before any read
        admission, outcome = await relay._owned(org, handle)
        # A gap, an expired journal or a cursor the journal never issued is a status, so it
        # is asked before the headers; the pump then reads from the same cursor.
        await relay.stream.read_owned(org, handle, cursor, 1)
        return _Events(relay, jobs.job_of(admission), cursor, jobs.state_of(admission, outcome))

    @app.delete(JOB_PATH)
    @guarded
    async def cancel_job(request: Request, request_id: str):
        org, handle = await jobs.owner(request)
        # Decided from the headers, never by reading: a DELETE carries no body.
        if request.headers.get("content-length", "0") != "0" \
                or "transfer-encoding" in request.headers:
            raise errors.InvalidRequest("DELETE /v1/jobs/{handle} takes no body")
        admission, _ = await relay._owned(org, handle)
        # Never a 200 without the committed cancel: an outage is a retryable 503 (the job is
        # untouched), and the retried DELETE answers what is committed then.
        outcome = await _dependency(
            relay.cancel(org, handle, cause=TerminalCause.client_cancelled))
        return _answer(jobs.status_of(admission, outcome, await relay.jobs.db_now()), admission)

    # The guard's wrapper is defined in `intake`; the route table names this module
    # (`ingress.assert_route_table`).
    for endpoint in (create_job, job_status, job_result, job_events, cancel_job):
        endpoint.__module__ = __name__
    relay.on_async = jobs.accepted
    return jobs
