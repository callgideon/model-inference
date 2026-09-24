#!/usr/bin/env python3
"""PREP-WORKER (I2B-R5): the product preparation loop in `python -m infrx.worker`.

    uv run --frozen pytest -q tests/w/test_prep_worker.py
    # + the PostgreSQL cases (a D task, a Valkey and a MinIO of the lane's own; each skips
    #   visibly without them):
    INFRX_D_TASK=d5 INFRX_D2_VALKEY_PORT=55467 INFRX_D2_VALKEY_CONTAINER=infrx-d5-valkey \\
    INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 INFRX_M_S3_LOCAL_CREDS=1 \\
        uv run --frozen pytest -q tests/w/test_prep_worker.py

Service-free: the runner and the service on F's fakes (`FakeJobStore`, `FakeScheduler`, one
`FakeClock`), W1's real `VllmEngine` speaking to E2's `FakeVllmApp` in process (httpx's ASGI
transport: the routes the fake engine process serves, `/tokenize` included) and M's real
`MediaPreparation` over an in-memory object store and a processing cache in tmp; the durable
attach another process reads is a dict the case fills. On PostgreSQL: the real `python -m
infrx.worker` (both pools) beside the gateway composed in the test's process, with the
gateway's relay (Q3's `Reconciler`, which the gateway's lifespan runs and httpx's ASGI
transport does not) run by the case.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import pathlib
import time

import httpx
import pytest

from infrx.config import RuntimeMisconfigured, from_env
from infrx.contracts import errors, ports
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import credit_jobstore_factory, jobstore_factory
from infrx.contracts.fakes.scheduling import FakeScheduler
from infrx.contracts.fakes.state import FakeJobStore
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import IndexEvent, JobState, OutboxKind
from infrx.contracts.v2 import fixtures as v2fix
from infrx.media.attachments import PgAttachments
from infrx.media.prepare import MediaPreparation, ProcessingCache
from infrx.media.store import InMemoryObjectStore
from infrx.state.jobstore import PgJobStore
from infrx.worker import VllmEngine, WorkerLoop, WorkerService, prepared_request
from infrx.worker import __main__ as worker_main
from infrx.worker.fakes import m2_local_uri
from infrx.worker.preparation import (PreparationRunner, PreparationResult, VIDEO_TOKEN_ID,
                                      engine_prompt_tokens)
from tests.m.support import mp4
from tests.w.test_engine import Box as ClockBox
from tests.w.test_engine import video_work
from tests.w.test_worker_main import (Box, answer, composed, environment, fake_vllm_module,
                                      start_worker, stop_worker)

COUNT = 1337              # the fake engine's count here: not its default 1200, nobody's constant
CLIP = mp4(seconds=10.0)
DATA_URL = "data:video/mp4;base64," + base64.b64encode(CLIP).decode()


def run(coro):
    return asyncio.run(coro)


async def outcome(coro):
    """The call's answer, or the class of what it raised: a case then ASSERTS the type, so a
    defect that lets an untyped error escape fails as an assertion, never as a crash."""
    try:
        return await coro
    except Exception as refused:                  # noqa: BLE001 - the point is the class
        return type(refused)


async def until(check, within_s: float):
    """`await check()`'s first truthy answer, polled; its last answer at the bound."""
    deadline = time.monotonic() + within_s
    while True:
        value = await check()
        if value or time.monotonic() > deadline:
            return value
        await asyncio.sleep(0.02)


async def within(coro, seconds: float = 10.0):
    """`coro`'s answer, or "timed out": a hang is an assertion here, not a stuck run."""
    try:
        async with asyncio.timeout(seconds):
            return await coro
    except TimeoutError:
        return "timed out"


class Prep:
    """F's fake store and index on one clock, E2's fake engine in process, M's preparation
    over an in-memory object store and a processing cache of the case's own."""

    def __init__(self, tmp_path, *, limits=DEFAULTS, credit: bool = False,
                 transport=None, **runner) -> None:
        self.harness = (credit_jobstore_factory if credit else jobstore_factory)(limits)
        self.store, self.clock, self.credit = self.harness.port, self.harness.clock, credit
        self.jobs = worker_main.CreditWork(self.store) if credit else self.store
        self.scheduler = FakeScheduler(self.clock, limits=limits)
        self.app = fake_vllm_module().FakeVllmApp(prompt_tokens=COUNT)
        self.root = str(tmp_path / "cache")
        os.makedirs(self.root, exist_ok=True)
        self.attached: dict[str, tuple] = {}      # the durable attach (PgAttachments' role)
        self.media = MediaPreparation(InMemoryObjectStore(), limits=limits,
                                      cache=ProcessingCache(self.root), attachments=self)
        client = httpx.AsyncClient(transport=transport or httpx.ASGITransport(app=self.app),
                                   base_url="http://engine")
        self.engine = VllmEngine(client, served_model=worker_main.SERVED_MODEL,
                                 clock=self.clock, limits=limits, local_uri=self.media.local_uri,
                                 local_media_root=self.root)
        # The product's attach wait (ATTACH_WAIT_S) and renewal cadence unless a case names
        # its own (review L2/L6: the defaults are what the pilot runs).
        self.runner = PreparationRunner(jobs=self.jobs, media=self.media, engine=self.engine,
                                        worker_id="prep-w", limits=limits, **runner)

    async def get(self, job_id):                   # `attachments.get`
        return self.attached.get(job_id)

    async def admit(self, *, video: bool = False, org_id: str = b.ORG_A):
        refs = ((await self.media.materialize(org_id, DATA_URL)),) if video else ()
        if self.credit:
            request = b.request(self.harness, org_id=v2fix.IDS.consumer_org,
                                key_id=v2fix.IDS.consumer_key,
                                model_revision=v2fix.REQUESTED_MODEL, refs=refs)
            await self.store.admit_credit(request, b.idem(request, request.request_id))
        else:
            self.harness.extra["grant"](org_id, "25.00")
            request = b.request(self.harness, org_id=org_id, refs=refs)
            await self.store.admit(request, b.idem(request, request.request_id), ())
        return request

    def candidate(self, request) -> IndexEvent:
        return IndexEvent(event_id=self.harness.ids.event_id(), job_id=request.request_id,
                          org_id=request.org_id, key_id=request.key_id,
                          kind=OutboxKind.prepare_dispatch,
                          execution_mode=request.execution_mode,
                          available_at=self.clock.now(), attempt=1)

    async def state(self, request) -> JobState:
        return self.store.jobs[request.request_id].state

    async def work_after(self, request):
        """What the inference lease holder reads once the job is queued (or the class of the
        refusal when it is not: the case's assertion on the attempt comes first)."""
        async def read():
            lease = await self.store.claim(request.request_id, "worker-a")
            return await self.jobs.load_work(lease)
        return await outcome(read())


# ------------------------------------------------------------------ the contract

def test_prep_worker__prepared_takes_a_keyword_count_on_every_adapter():
    """The ruling candidate's shape: `prepared(lease, media=(), *, prompt_tokens=None)` on the
    port, F's fake and D's PostgreSQL store - keyword-only, so no positional caller can put
    a count where the refs go, and absent means no count."""
    for operation in (ports.JobStore.prepared, FakeJobStore.prepared, PgJobStore.prepared):
        parameters = inspect.signature(operation).parameters
        count = parameters["prompt_tokens"]
        assert count.kind is inspect.Parameter.KEYWORD_ONLY, operation.__qualname__
        assert count.default is None and parameters["media"].default == (), \
            operation.__qualname__


# ------------------------------------------------------------------ the runner

@pytest.mark.parametrize("credit", [False, True], ids=["legacy", "credit"])
def test_prep_worker__a_text_job_is_queued_with_the_engines_own_count(tmp_path, credit, caplog):
    """A text job: the preparation lease, the request read through it (the CREDIT door in the
    CREDIT regime), the engine's `/tokenize` asked for exactly the chat body the engine will
    be sent - the served model, the rebuilt messages, the generation prompt, nothing else -
    and its count stored by `prepared`: the inference lease holder reads it back. The worker
    logs the preparation at INFO with the count (review J-F2)."""
    caplog.set_level(logging.INFO, logger="infrx.worker")
    prep = Prep(tmp_path, credit=credit)

    async def case():
        request = await prep.admit()
        result = await prep.runner.run(request.request_id)
        return request, result, await prep.work_after(request)

    request, result, work = run(case())
    assert (result.cause, result.refusal, result.prompt_tokens) == ("prepared", None, COUNT), \
        result
    assert work.prompt_tokens == COUNT and work.prepared_refs == ()
    assert prep.app.tokenized == [{"model": "marlin2b", "add_generation_prompt": True,
                                   "messages": [{"role": "user",
                                                 "content": "Describe this clip."}]}]
    logged = [record.getMessage() for record in caplog.records
              if record.levelno == logging.INFO and record.name == "infrx.worker"]
    assert any(line.startswith(f"prepared {request.request_id}: {COUNT} prompt tokens "
                               "(engine /tokenize, ") for line in logged), logged


def test_prep_worker__a_video_job_is_prepared_from_its_durable_attach(tmp_path):
    """A video job: the attach lands after the claim (as the gateway's does, just after the
    admission commits) and the runner waits for it; M's `prepare` re-reads the staged object,
    writes the prepared one and the processing-cache file; the engine counts the prompt with
    that file and the pinned budget (`mm_processor_kwargs` from the measured duration), and
    the prepared refs and the count are what the inference lease holder reads."""
    prep = Prep(tmp_path)

    async def case():
        request = await prep.admit(video=True)

        async def gateway_attaches():
            await asyncio.sleep(0.2)
            prep.attached[request.request_id] = request.media
        attaching = asyncio.create_task(gateway_attaches())
        result = await prep.runner.run(request.request_id)
        await attaching
        return request, result, await prep.work_after(request)

    request, result, work = run(case())
    assert (result.cause, result.prompt_tokens) == ("prepared", COUNT), result
    source, = request.media
    prepared, = work.prepared_refs
    assert work.prompt_tokens == COUNT and work.media_refs == (source,)
    assert prepared.storage_ref.endswith("/prepared") and prepared.digest == source.digest
    assert prepared.duration_s == 10.0 and prepared.mime == "video/mp4"
    path = prep.media.cache.path_for(source.org_id, "v1", source.digest, "video/mp4")
    assert pathlib.Path(path).read_bytes() == CLIP
    asked, = prep.app.tokenized
    part = asked["messages"][0]["content"][1]
    assert part == {"type": "video_url", "video_url": {"url": "file://" + path}}, part
    assert asked.get("mm_processor_kwargs") == prep.engine._media.budget_kwargs(10.0), asked


def test_prep_worker__a_job_whose_attach_never_lands_prepares_nothing(tmp_path):
    """No durable attach within the bound: `not_found`, nothing prepared or counted, the
    job still `preparing` (its lease left to lapse for `recover`)."""
    prep = Prep(tmp_path, attach_wait_s=0.2)

    async def case():
        request = await prep.admit(video=True)
        return request, await within(prep.runner.run(request.request_id), 5.0), \
            await prep.state(request)

    request, result, state = run(case())
    assert isinstance(result, PreparationResult) and result.refusal == "not_found", result
    assert state is JobState.preparing and prep.app.tokenized == []


def test_prep_worker__a_media_ref_of_another_org_is_not_found(tmp_path):
    """The attach names media of another organization than the job's (PostgreSQL's foreign
    keys forbid it; this is the worker's own guard): the engine's view of the request refuses
    it `not_found` before anything is counted or recorded on the job."""
    prep = Prep(tmp_path)

    async def case():
        own = await prep.admit(video=True)             # this org's clip, prepared and cached
        prep.attached[own.request_id] = own.media
        assert (await prep.runner.run(own.request_id)).cause == "prepared"
        request = await prep.admit(video=True)
        foreign = await prep.media.materialize(b.ORG_B, DATA_URL)
        prep.attached[request.request_id] = (foreign,)
        return await prep.runner.run(request.request_id), await prep.state(request)

    result, state = run(case())
    assert result.refusal == "not_found", result
    assert state is JobState.preparing and len(prep.app.tokenized) == 1


def tokenizer(*answers):
    """An engine client whose `/tokenize` answers each of `answers` in turn: a JSON body,
    an `httpx.Response`, or a coroutine function awaited first."""
    queue = list(answers)

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ready": True})
        assert request.url.path == "/tokenize", request.url
        answer = queue.pop(0)
        if callable(answer):
            answer = await answer()
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)
    return httpx.MockTransport(handle)


def counted(count: int, pads: int = 0) -> dict:
    return {"count": count, "tokens": [VIDEO_TOKEN_ID] * pads + [1] * (count - pads)}


TEXT_ANSWERS = {
    "unavailable": httpx.Response(503, json={"error": "down"}),
    "not-json": httpx.Response(200, content=b"<html>"),
    "no-count": {"tokens": [1, 2]},
    "bool-count": {"count": True},
    "negative": {"count": -1, "tokens": []},
    "text-count": {"count": "12"},
    "disagreeing": {"count": 3, "tokens": [1, 2]},
    "no-tokens": {"count": 5},                    # review L4: vLLM always answers both
}
# a 12.5 s clip (the builders' ref): 26 frames, 13 two-frame patches, at most 13 x 196
VIDEO_ANSWERS = {"unexpanded": counted(40, 1), "below-one-per-patch": counted(40, 12),
                 "past-the-budget": counted(3000, 2549), "no-tokens": {"count": 3000}}


@pytest.mark.parametrize("name", sorted(TEXT_ANSWERS) + sorted(VIDEO_ANSWERS))
def test_prep_worker__the_engines_answer_is_checked_and_never_guessed(name):
    """`/tokenize`'s answer is the count only when it is one: an integer and exactly that many
    tokens, and for a video between one `video_token_id` per two-frame patch and the pinned
    budget (196 per patch). Every other answer - the engine down, not JSON, no count, a bool,
    a negative, text, a disagreement, no tokens, one unexpanded placeholder, fewer than the
    patches, more than the budget - is `dependency_unavailable`, never a number."""
    box, root = ClockBox(), "/srv/infrx-cache"
    video = name in VIDEO_ANSWERS
    work = video_work(box) if video else video_work(box, refs=(), messages=(
        {"role": "user", "content": "Describe this clip."},))
    engine = VllmEngine(httpx.AsyncClient(transport=tokenizer(
        (VIDEO_ANSWERS if video else TEXT_ANSWERS)[name]), base_url="http://engine"),
        served_model="marlin2b", clock=box.clock, local_uri=m2_local_uri(root),
        local_media_root=root)
    got = run(outcome(engine_prompt_tokens(engine, prepared_request(work, 0))))
    assert got is errors.DependencyUnavailable, (name, got)


@pytest.mark.parametrize("pads", [13, 1300, 2548], ids=["one-per-patch", "between", "budget"])
def test_prep_worker__a_video_count_inside_the_pinned_budget_is_the_count(pads):
    """The two bounds are inclusive: 13 patches of a 12.5 s clip, and 13 x 196."""
    box, root = ClockBox(), "/srv/infrx-cache"
    engine = VllmEngine(httpx.AsyncClient(transport=tokenizer(counted(3000, pads)),
                                          base_url="http://engine"),
                        served_model="marlin2b", clock=box.clock, local_uri=m2_local_uri(root),
                        local_media_root=root)
    got = run(outcome(engine_prompt_tokens(engine, prepared_request(video_work(box), 0))))
    assert got == 3000, (pads, got)


@pytest.mark.parametrize("fault,video", [("down", False), ("unexpanded", True)])
def test_prep_worker__a_tokenizer_that_cannot_count_prepares_nothing(tmp_path, fault, video):
    """E2's fake engine with its tokenizer down (text) or answering one unexpanded
    placeholder per video: the attempt is refused `dependency_unavailable` and the job stays
    `preparing`. And the fake's count IS the usage its chat route reports."""
    prep = Prep(tmp_path)
    prep.app.control({"tokenize_fault": fault})

    async def case():
        request = await prep.admit(video=video)
        prep.attached[request.request_id] = request.media
        result = await outcome(prep.runner.run(request.request_id))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=prep.app),
                                     base_url="http://engine") as client:
            chat = (await client.post("/v1/chat/completions", json={"messages": []})).json()
        return result, await prep.state(request), chat

    result, state, chat = run(case())
    assert isinstance(result, PreparationResult), result          # answered, not raised
    assert result.refusal == "dependency_unavailable", result
    assert state is JobState.preparing and len(prep.app.tokenized) == 1
    assert chat["usage"]["prompt_tokens"] == COUNT


def test_prep_worker__a_refused_attempt_is_requeued_when_its_lease_lapses(tmp_path):
    """A typed refusal prepares nothing and never guesses: the lease is left to lapse (a
    second claim meanwhile is `not_claimable`), `recover` then requeues the job with a FRESH
    `prepare_dispatch` row (R93; the gateway's relay delivers it), and the next attempt - the
    tokenizer back - queues it."""
    prep = Prep(tmp_path, renew_every_s=0.01)
    prep.app.control({"tokenize_fault": "down"})

    async def case():
        request = await prep.admit()
        first = await within(prep.runner.run(request.request_id), 5.0)
        held = await outcome(prep.store.claim_preparation(request.request_id, "another"))
        state = await prep.state(request)
        prep.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        await prep.store.recover()
        prep.app.control({"tokenize_fault": "none"})
        second = await prep.runner.run(request.request_id)
        return request, first, held, state, second, await prep.work_after(request)

    request, first, held, state, second, work = run(case())
    assert isinstance(first, PreparationResult), first            # the attempt ENDED
    assert first.refusal == "dependency_unavailable" and state is JobState.preparing, first
    assert held is errors.NotClaimable
    dispatched = [event.event_id for event in prep.store.outbox
                  if event.aggregate_id == request.request_id
                  and event.kind is OutboxKind.prepare_dispatch]
    assert len(dispatched) == len(set(dispatched)) == 2, dispatched
    assert second.cause == "prepared" and work.prompt_tokens == COUNT


def test_prep_worker__a_tokenizer_that_never_answers_is_bounded_by_the_budget(tmp_path):
    """An engine that never answers `/tokenize` holds a preparation runner no longer than
    `PREPARATION_TIMEOUT_S` (0.3 s here): the attempt is refused `dependency_unavailable`
    and the job stays `preparing` for `recover`."""
    async def never():
        await asyncio.Event().wait()
    prep = Prep(tmp_path, limits=DEFAULTS.replace(preparation_timeout_s=0.3),
                transport=tokenizer(never))

    async def case():
        request = await prep.admit()
        return await within(prep.runner.run(request.request_id), 5.0), await prep.state(request)

    result, state = run(case())
    assert isinstance(result, PreparationResult), result
    assert result.refusal == "dependency_unavailable" and state is JobState.preparing, result


def test_prep_worker__the_lease_is_renewed_while_preparation_runs(tmp_path):
    """R52: a preparation that outlives the lease TTL keeps its lease - renewed while the
    engine counts - and still queues the job (40 s of store time against a 30 s lease)."""
    release = asyncio.Event
    gate = {}

    async def slow():
        gate["asked"].set()
        await gate["release"].wait()
        return httpx.Response(200, json=counted(COUNT))
    prep = Prep(tmp_path, transport=tokenizer(slow), renew_every_s=0.01)

    async def case():
        gate.update(asked=release(), release=release())
        request = await prep.admit()
        attempt = asyncio.create_task(prep.runner.run(request.request_id))
        await gate["asked"].wait()
        for _ in range(2):
            prep.clock.advance(20)
            await asyncio.sleep(0.1)          # the renewer runs
        gate["release"].set()
        return await attempt

    result = run(case())
    assert (result.cause, result.refusal) == ("prepared", None), result


def test_prep_worker__the_default_renewal_keeps_a_long_preparation_alive(tmp_path):
    """Review L2: the PRODUCT cadence (every third of `PREPARATION_LEASE_TTL_S`, no injected
    interval) keeps a preparation alive through several TTLs: 0.3 s leases, the store's
    clock moved 1.6 s in 0.2 s steps while the engine counts, and the job is still queued."""
    gate = {}

    async def slow():
        gate["asked"].set()
        await gate["release"].wait()
        return httpx.Response(200, json=counted(COUNT))
    prep = Prep(tmp_path, limits=DEFAULTS.replace(preparation_lease_ttl_s=0.3),
                transport=tokenizer(slow))

    async def case():
        gate.update(asked=asyncio.Event(), release=asyncio.Event())
        request = await prep.admit()
        attempt = asyncio.create_task(prep.runner.run(request.request_id))
        await gate["asked"].wait()
        for _ in range(8):
            await asyncio.sleep(0.15)             # at least one renewal (every 0.1 s)
            prep.clock.advance(0.2)
        gate["release"].set()
        return await attempt, await prep.state(request)

    result, state = run(case())
    assert (result.cause, result.refusal, state) == ("prepared", None, JobState.queued), result


class Unreachable:
    """The store with its `heartbeat` failing untyped (the database gone mid-attempt)."""

    def __init__(self, store) -> None:
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def heartbeat(self, lease):
        raise RuntimeError("the store did not answer")


def test_prep_worker__an_untyped_renewal_failure_is_not_swallowed(tmp_path):
    """Review L7: an untyped failure of the lease renewal propagates out of the runner (the
    crash-only service then drains and exits) - it is never swallowed while the attempt
    carries on unrenewed; a typed refusal only stops the renewals."""
    async def slow():
        await asyncio.sleep(0.1)                  # several renewals' worth
        return httpx.Response(200, json=counted(COUNT))
    prep = Prep(tmp_path, transport=tokenizer(slow), renew_every_s=0.01)
    prep.runner.jobs = Unreachable(prep.store)

    async def case():
        request = await prep.admit()
        return await outcome(prep.runner.run(request.request_id))

    assert run(case()) is RuntimeError


# ------------------------------------------------------------------ the service

class Dead(PreparationRunner):
    """A preparation runner that dies untyped: crash-only, the service must notice."""

    async def run(self, job_id):
        raise RuntimeError("the preparation runner died")


class Idle:
    """The inference runner of a pool that is never offered a candidate."""

    async def run(self, job_id):                          # pragma: no cover - never called
        raise AssertionError("no inference candidate was enqueued")


def service(prep: Prep, limits=DEFAULTS, runner=None) -> WorkerService:
    return WorkerService(
        loop=WorkerLoop(scheduler=prep.scheduler, runner=Idle(), worker_id="prep-w",
                        limits=limits),
        preparation=WorkerLoop(scheduler=prep.scheduler, runner=runner or prep.runner,
                               worker_id="prep-w", kind=OutboxKind.prepare_dispatch,
                               limits=limits),
        preparation_concurrency=2, jobs=prep.store, engine=prep.engine)


def test_prep_worker__the_service_prepares_and_drains_its_preparation_pool(tmp_path, caplog):
    """The W3 service runs the preparation pool beside the inference pool: a `prepare_dispatch`
    candidate is prepared (the job queued, the readiness body counting it); at a drain an
    attempt still counting at `PREPARATION_LEASE_TTL_S` is released - cancelled, never
    prepared, the job left `preparing` for `recover` - within that bound, on its own log line,
    and the inference pool's line is unchanged."""
    caplog.set_level(logging.WARNING, logger="infrx.worker")
    limits = DEFAULTS.replace(preparation_lease_ttl_s=0.5)

    async def answer():
        return httpx.Response(200, json=counted(COUNT))

    async def hang():
        await asyncio.Event().wait()

    async def after_the_signal():                 # answers inside the drain bound (0.5 s)
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=counted(COUNT))
    prep = Prep(tmp_path, limits=limits, transport=tokenizer(answer, hang, after_the_signal))
    worker = service(prep, limits)

    async def case():
        first, second, third = await prep.admit(), await prep.admit(), await prep.admit()
        await worker.start()
        await prep.scheduler.enqueue(prep.candidate(first))

        async def is_queued():
            return await prep.state(first) is JobState.queued
        queued = await within(until(is_queued, 5.0), 6.0)
        body = await worker.readiness()
        await prep.scheduler.enqueue(prep.candidate(second))
        await within(until(_in_flight(worker, 1), 5.0), 6.0)
        await prep.scheduler.enqueue(prep.candidate(third))
        busy = await within(until(_in_flight(worker, 2), 5.0), 6.0)
        began = time.monotonic()
        stopped = await within(worker.stop(), 5.0)
        return first, second, third, queued, body, busy, time.monotonic() - began, stopped, \
            await prep.state(second), await prep.state(third)

    first, second, third, queued, body, busy, took, stopped, left, done = run(case())
    assert queued is True and body["prepare_claimed"] == 1, (queued, body)
    assert busy and stopped != "timed out" and took < 3.0, (busy, stopped, took)
    assert (left, done) == (JobState.preparing, JobState.queued), (left, done)
    lines = [record.getMessage() for record in caplog.records]
    assert (f"drained preparation: 1 finished, 1 released ['{second.request_id}'], "
            f"ended [('{third.request_id}', 'prepared')]") in lines, lines
    assert "drained: 1 finished, 0 released [], ended []" in lines, lines


def test_prep_worker__a_candidate_offered_twice_is_a_lost_claim_not_a_dead_runner(tmp_path):
    """Review L1: the index is a hint (02 §4) - a relay redelivery or a reconciler repair can
    offer one job's `prepare_dispatch` twice. The second claim is refused `not_claimable` and
    answered: no runner dies, the service stays live, the job is prepared once."""
    prep = Prep(tmp_path)
    worker = service(prep)

    async def case():
        request = await prep.admit()
        await worker.start()
        for _ in range(2):
            await prep.scheduler.enqueue(prep.candidate(request))

        async def both_answered():
            return len(worker.preparation.results) == 2
        answered = await within(until(both_answered, 5.0), 6.0)
        body = await worker.readiness()
        died = list(worker._died())
        await worker.stop()
        return request, answered, body, died

    request, answered, body, died = run(case())
    outcomes = sorted((result.cause or "", result.refusal or "")
                      for result in worker.preparation.results)
    assert answered is True and outcomes == [("", "not_claimable"), ("prepared", "")], outcomes
    assert died == [] and body["live"] is True, (died, body)
    assert prep.store.jobs[request.request_id].preparation_attempts == 1


def _in_flight(worker, count: int = 1):
    async def check():
        return (await worker.readiness())["preparing"] == count
    return check


def test_prep_worker__a_dead_preparation_runner_ends_the_service(tmp_path):
    """Crash-only (W3): a preparation runner that dies untyped makes `serve()` drain and
    return, and the death is visible - `/livez` false, the process exits 1."""
    prep = Prep(tmp_path)
    worker = service(prep, runner=Dead(jobs=prep.jobs, media=prep.media, engine=prep.engine,
                                       worker_id="prep-w"))

    async def case():
        request = await prep.admit()
        await prep.scheduler.enqueue(prep.candidate(request))
        report = await within(worker.serve(asyncio.Event()), 10.0)
        return report, await worker.readiness()

    report, body = run(case())
    assert report != "timed out" and worker._died(), report
    assert body["live"] is False and body["runners_dead"] >= 1, body


# ------------------------------------------------------------------ the composition

def test_prep_worker__the_worker_composes_the_preparation_pool(tmp_path):
    """`python -m infrx.worker` runs the preparation pool on `prepare_dispatch`, from the same
    index, with `PREPARATION_CONCURRENCY` runners, through the same work doors as the
    inference runner (`CreditWork` in the CREDIT regime), the same engine, M's
    `MediaPreparation` over the object store, the shared `PROCESSING_CACHE_DIR` and the
    durable attach D2's tables record (`PgAttachments`)."""
    objects = InMemoryObjectStore()
    env = environment(tmp_path, ACCOUNTING_REGIME="credit", PREPARATION_CONCURRENCY="3",
                      ACTIVE_RATE_CARD_VERSION=v2fix.BUILDERS["rate_card_marlin.json"]()
                      .rate_card_version)
    worker, _pool = composed(env, objects=objects)
    pool = worker.preparation
    assert isinstance(pool, WorkerLoop) and isinstance(pool.runner, PreparationRunner), pool
    runner = pool.runner
    assert pool.kind is OutboxKind.prepare_dispatch
    assert pool.scheduler is worker.loop.scheduler and worker.preparation_concurrency == 3
    assert runner.jobs is worker.jobs and isinstance(runner.jobs, worker_main.CreditWork)
    assert runner.engine is worker.engine and runner.worker_id == worker.loop.worker_id
    assert runner.media.objects is objects and isinstance(runner.media.attachments,
                                                          PgAttachments)
    assert runner.media.cache.root == str(tmp_path / "cache")
    assert worker.engine.local_uri == runner.media.local_uri


def test_prep_worker__a_media_root_the_worker_cannot_write_refuses_startup(tmp_path):
    """Preparation materializes media into `PROCESSING_CACHE_DIR`, so a root this process can
    read but not write refuses startup, naming the setting."""
    if os.geteuid() == 0:
        pytest.skip("root writes a read-only directory")
    env = environment(tmp_path)
    os.chmod(env["PROCESSING_CACHE_DIR"], 0o555)
    try:
        refused = run(outcome(asyncio.to_thread(composed, env)))
    finally:
        os.chmod(env["PROCESSING_CACHE_DIR"], 0o755)
    assert refused is RuntimeMisconfigured


def test_prep_worker__a_zero_preparation_pool_refuses_startup_by_name(tmp_path):
    """Review L3: `PREPARATION_CONCURRENCY=0` cannot start a preparation pool, so the worker
    process refuses it before anything binds - exit 2, the setting named, no traceback, as
    `WORKER_CONCURRENCY` - and the installer refuses the value first (`positive_int`)."""
    from tests.i.support import preflight
    log = tmp_path / "zero.log"
    process = start_worker(environment(tmp_path, INFRX_MODE="dev",
                                       PREPARATION_CONCURRENCY="0"), log)
    code, text = process.wait(timeout=60), log.read_text()
    assert code == worker_main.REFUSED and "PREPARATION_CONCURRENCY" in text \
        and "Traceback" not in text, (code, text)
    refused = preflight.tunables(("PREPARATION_CONCURRENCY=0",), {})
    assert [problem for problem in refused if "positive_int" in problem], refused
    assert preflight.tunables(("PREPARATION_CONCURRENCY=2",), {}) == []


# --- on PostgreSQL: the D harness, a Valkey and a MinIO of the lane's own ------------

@pytest.fixture
def box(tmp_path, monkeypatch):
    made = Box(tmp_path, monkeypatch)
    yield made
    made.close()


def job_row(box: Box, request_id: str) -> tuple:
    return box.conn.execute(
        "select state, outcome_cause, prepared_prompt_tokens, preparation_attempts, "
        "(select count(*) from infrx.attempts where job_id = j.request_id "
        "and released_at is null) from infrx.jobs j where request_id = %s",
        (request_id,)).fetchone()


async def terminal(client, handle: str, within_s: float = 60.0):
    auth = {"authorization": "Bearer sk-i2b"}

    async def check():
        status = (await client.get(f"/v1/jobs/{handle}", headers=auth)).json()
        return status if status["state"] in ("succeeded", "failed", "cancelled") else None
    deadline = time.monotonic() + within_s
    while True:
        found = await check()
        if found or time.monotonic() > deadline:
            return found
        await asyncio.sleep(0.1)


def test_prep_worker_pg__the_worker_process_prepares_and_runs_an_admitted_job(box):
    """The pilot's path with nothing emulated: the gateway admits a CREDIT job and its relay
    indexes the `prepare_dispatch`; the worker PROCESS's preparation pool claims it, counts
    the prompt with the engine's `/tokenize` (1337: the fake engine's count, set here) and
    queues it; its inference pool runs and settles it. The stored count, the settled usage
    and the engine's count are one number; no attempt is left unreleased; SIGTERM drains
    both pools and exits 0."""
    box.start_engine()
    box.engine.control(prompt_tokens=COUNT)
    box.start_worker()

    async def case():
        ready = await answer(box.port, "/readyz", 200, within_s=60)
        async with box.relay(), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=box.gateway()), base_url="http://gw") as client:
            handle, request_id = await box.admit(client)
            status = await terminal(client, handle)
        return ready, request_id, status

    ready, request_id, status = run(case())
    log = box.log.read_text()
    assert ready[0] == 200, (ready, log)
    assert status and (status["state"], status["cause"]) == ("succeeded", "completed"), \
        (status, log)
    assert job_row(box, request_id) == ("succeeded", "completed", COUNT, 1, 0), log
    usage = box.conn.execute("select prompt_tokens, accounting_regime from "
                             "public.usage_events where id = %s", (request_id,)).fetchone()
    assert usage == (COUNT, "credit") and status["usage"]["prompt_tokens"] == COUNT
    assert f"INFO infrx.worker prepared {request_id}: {COUNT} prompt tokens (engine /tokenize, " \
        in log, log                                          # review J-F2: the log proves it
    assert stop_worker(box.worker) == 0, box.log.read_text()
    assert "drained preparation: 2 finished, 0 released [], ended []" in box.log.read_text()


def test_prep_worker_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it(
        box, tmp_path):
    """OPS-RECOVER for preparation: SIGTERM while an attempt waits on the engine's tokenizer
    releases it at `PREPARATION_LEASE_TTL_S` (2 s here) - the process exits 0 and logs the
    release - and the job is not left behind a dead lease: once it lapses, the NEXT worker
    process's reaper requeues it (a fresh `prepare_dispatch`) and prepares and runs it."""
    box.settings["PREPARATION_LEASE_TTL_S"] = "2"
    box.start_engine()
    box.engine.control(prompt_tokens=COUNT, tokenize_delay_s=60)
    box.start_worker()

    async def case():
        await answer(box.port, "/readyz", 200, within_s=60)
        async with box.relay(), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=box.gateway()), base_url="http://gw") as client:
            handle, request_id = await box.admit(client)

            async def counting():
                return box.engine.control()["tokenized"] >= 1
            await until(counting, 30.0)
            began = time.monotonic()
            code = await asyncio.to_thread(stop_worker, box.worker)
            took, first = time.monotonic() - began, box.log.read_text()
            left = job_row(box, request_id)
            box.engine.control(tokenize_delay_s=0)
            box.log = tmp_path / "worker-2.log"
            box.start_worker()
            status = await terminal(client, handle, within_s=90)
        return request_id, code, took, first, left, status

    request_id, code, took, first, left, status = run(case())
    assert code == 0 and took < 30, (code, took, first)
    assert f"drained preparation: 1 finished, 1 released ['{request_id}'], ended []" in first, \
        first
    assert left[:3] == ("preparing", None, None), left
    assert status and status["state"] == "succeeded", (status, box.log.read_text())
    assert job_row(box, request_id) == ("succeeded", "completed", COUNT, 2, 0)


def test_prep_worker_pg__a_tokenize_count_that_disagrees_with_the_usage_settles_at_the_usage(
        box):
    """Review L8: the stored count and the settled usage are one number only while the engine
    tokenizes as it generates. With E2's fake answering `/tokenize` 1000 while its chat route
    reports 1337 prompt tokens, the job is prepared with 1000 (`prepared_prompt_tokens`: the
    context check's number) and SETTLED at the engine's usage, 1337 (D5 charges usage, never
    the prepared count) - so a tokenizer that disagrees mis-states the context check, not the
    charge."""
    box.start_engine()
    box.engine.control(prompt_tokens=COUNT, tokenize_count=1000)
    box.start_worker()

    async def case():
        await answer(box.port, "/readyz", 200, within_s=60)
        async with box.relay(), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=box.gateway()), base_url="http://gw") as client:
            handle, request_id = await box.admit(client)
            return request_id, await terminal(client, handle)

    request_id, status = run(case())
    assert status and status["state"] == "succeeded", (status, box.log.read_text())
    assert job_row(box, request_id) == ("succeeded", "completed", 1000, 1, 0)
    usage = box.conn.execute("select prompt_tokens from public.usage_events where id = %s",
                             (request_id,)).fetchone()
    assert usage == (COUNT,) and status["usage"]["prompt_tokens"] == COUNT, (usage, status)


def test_prep_worker_pg__a_count_past_postgresql_int_is_context_length_exceeded():
    """Review L4: a count PostgreSQL's `int` cannot hold is refused typed
    (`context_length_exceeded`, as the fake answers), never an untyped driver error that
    would kill the worker; the job stays `preparing`."""
    from tests.d import pgharness, pgstore
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PREP-WORKER: the D harness is unavailable: {reason}")

    async def case():
        harness = pgstore.factory()
        harness.extra["grant"](b.ORG_A, "25.00")
        request = b.request(harness)
        admission = await harness.port.admit(request, b.idem(request), ())
        lease = await harness.port.claim_preparation(request.request_id, "prep-a")
        refused = await outcome(harness.port.prepared(lease, (), prompt_tokens=2**31))
        left, _ = await harness.port.get_owned(request.org_id, admission.job_handle)
        return refused, left.state

    refused, state = run(case())
    assert (refused, state) == (errors.ContextLengthExceeded, JobState.preparing), refused
