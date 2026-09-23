#!/usr/bin/env python3
"""API-MODES / DUR-ADMIT (G half) / DUR-FENCE (G half): G3's explicit async jobs.

    uv run --frozen pytest -q -p no:cacheprovider tests/g/jobs

The cutover's app with the jobs router mounted (`world.JobsWorld`): the metered ingress and
G2's `Relay` over the contract fakes (JobStore + StreamStore, legacy or CREDIT), M's real
media adapter and W's real attempt runner over the scripted engine. Every case asserts store
state - job, hold, outbox, journal - not only the HTTP answer. `World.during` is what the
store does while the gateway waits (one step per poll): a case whose mutant would turn a 202
into a synchronous wait lets that wait end instead of hanging.

Implemented on fakes: D2's `PgJobStore` and D4's `PgStreamStore` are the integration target.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from infrx.config import RuntimeMisconfigured
from infrx.contracts import errors, fixtures, wire
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.state import FakeStreamStore
from infrx.contracts.records import (ChunkEventType, EngineEvent, ExecutionMode, HoldState,
                                     JobState, OutboxKind, SettlementState, TerminalCause,
                                     TerminalOutcome, Usage)
from infrx.gateway import pilot
from infrx.gateway.routes import ingress, jobs as jobs_router
from infrx.gateway.routes.relay import CREDIT
from infrx.media.store import InMemoryObjectStore
from infrx.observe.metrics import Registry
from infrx.operations import service
from infrx.scheduling.memory import MemoryScheduler

from .. import relay_support as rs
from .world import FIXED_ID, OTHER_ROW, JobsWorld, job_path, send

CHAT = rs.support.CHAT_PATH
JOBS = jobs_router.JOBS_PATH
PREFER = {"prefer": "respond-async"}


def post(world, path=JOBS, payload=None, **kw):
    return rs.run(send(world.app, "POST", path, body=payload or rs.body(), **kw))


def get(world, path, **kw):
    return rs.run(send(world.app, "GET", path, **kw))


def refusal(reply):
    """(status, error code) of a reply, read so that a reply that is not an error - a 202, a
    stream - fails the comparison rather than raising."""
    if not reply.headers.get("content-type", "").startswith("application/json"):
        return reply.status, None
    return reply.status, (reply.json().get("error") or {}).get("code")


def a_wait_would_end(world):
    """What the store does if a mutant turns the 202 into a synchronous wait: time passes,
    so the wait ends (504) instead of polling for ever."""
    world.during.append(lambda: world.clock.advance(3_600))


# --- item 1: acceptance and the 202 ----------------------------------------------------
def test_api_modes__respond_async_on_chat_answers_202_only_after_the_commit():
    """`Prefer: respond-async` on chat: 202 `JobAccepted` (job_accepted.json's fields), the
    preference echoed, `Location`, `Retry-After`, `Inference-Id` - sent only once the job, its
    hold, its dispatch outbox and its attached refs are in the store. Counted as async."""
    world = JobsWorld()
    world.relay.registry = registry = Registry("gateway")
    a_wait_would_end(world)
    seen = []

    def on_send(message):
        if message["type"] == "http.response.start":
            seen.extend((job.state, world.jobs.holds[job.id].state,
                         world.jobs.outbox_kinds(job.id), job.id in world.media.by_job)
                        for job in world.jobs.jobs.values())

    reply = post(world, CHAT, rs.body(rs.VIDEO), headers=PREFER, on_send=on_send)
    assert reply.status == 202, reply.body
    assert len(world.jobs.jobs) == 1
    job = world.only_job()
    assert seen == [(JobState.preparing, HoldState.held, [OutboxKind.prepare_dispatch], True)]
    assert len(world.media.by_job[job.id]) == 1                    # the staged clip, attached
    body = reply.json()
    assert set(body) == set(fixtures.load("job_accepted.json"))
    accepted = wire.JobAccepted.model_validate(body)
    assert (accepted.job_handle, accepted.request_id, accepted.state, accepted.execution_mode,
            accepted.idempotency_replayed) == (job.admission.job_handle, job.id,
                                               JobState.preparing, ExecutionMode.async_, False)
    assert (accepted.created_at, accepted.deadline_at) == (job.admission.admitted_at,
                                                           job.admission.deadline_at)
    assert reply.headers.get(wire.HEADER_PREFERENCE_APPLIED) == wire.PREFER_RESPOND_ASYNC
    assert reply.headers.get(jobs_router.HEADER_LOCATION) == job_path(job.admission.job_handle)
    assert reply.headers.get(wire.HEADER_RETRY_AFTER) == str(jobs_router.POLL_AFTER_S)
    assert reply.headers.get(wire.HEADER_INFERENCE_ID) == job.id
    assert job.request.execution_mode is ExecutionMode.async_
    assert job.budgets.queue_wait_s == world.limits.queue_wait_async_s
    assert registry.value("infrx_jobs_accepted_total", mode="async", tenant=world.org) == 1


def test_api_modes__post_jobs_is_always_async_and_applies_no_preference():
    """`POST /v1/jobs` is async whatever `Prefer` says (the route is the preference, so no
    preference is reported applied); a streaming body is refused there, admitting nothing."""
    world = JobsWorld()
    a_wait_would_end(world)
    for n, headers in enumerate(({}, {"prefer": "wait=5"}, PREFER)):
        reply = post(world, headers=headers, key=f"k-{n}")
        assert reply.status == 202, (headers, reply.body)
        assert wire.HEADER_PREFERENCE_APPLIED not in reply.headers
        assert reply.json()["execution_mode"] == "async"
        handle = reply.json()["job_handle"]
        assert reply.headers.get(jobs_router.HEADER_LOCATION) == job_path(handle)
    assert len(world.jobs.jobs) == 3
    assert {job.request.execution_mode for job in world.jobs.jobs.values()} == {
        ExecutionMode.async_}
    refused = post(world, payload=rs.body(stream=True))
    assert refusal(refused) == (400, "invalid_request")
    assert refused.json()["error"].get("param") == "stream"        # what this client sent
    assert len(world.jobs.jobs) == 3


def test_dur_admit__a_lost_202_retried_with_its_key_answers_the_same_job():
    """The 202 dies on the wire. The retry under the same key answers the ORIGINAL acceptance
    as it stands now (job_idempotent_replay.json: `running`, replayed) - the same handle, id,
    created and deadline - and nothing is admitted, held or dispatched twice."""
    world = JobsWorld()

    def lose(message):
        if message["type"] == "http.response.start":
            raise OSError("connection reset by peer")

    with pytest.raises(OSError):
        post(world, key="order-7", on_send=lose)
    job = world.only_job()
    reserved = world.jobs.wallet(world.org).reserved_total
    lease = rs.run(world.lease())                                  # the job runs meanwhile
    lookups = world.failures.count("lookup")
    again = post(world, key="order-7")
    assert again.status == 202, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert again.headers.get(wire.HEADER_INFERENCE_ID) == job.id
    body = again.json()
    assert set(body) == set(fixtures.load("job_idempotent_replay.json"))
    assert (body["job_handle"], body["request_id"], body["state"], body["idempotency_replayed"]) \
        == (job.admission.job_handle, job.id, "running", True)
    accepted = wire.JobAccepted.model_validate(body)
    assert (accepted.created_at, accepted.deadline_at) == (job.admission.admitted_at,
                                                           job.admission.deadline_at)
    assert list(world.jobs.jobs) == [job.id] and list(world.jobs.holds) == [job.id]
    assert world.jobs.wallet(world.org).reserved_total == reserved
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.prepare_dispatch) == 1
    # R91: the retry is answered from the store's lookup, never admitted a second time.
    assert world.failures.count("lookup") == lookups + 1
    assert world.failures.count("admit") == 1
    rs.run(world.complete(lease))
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled


def staged_payloads(world) -> list[str]:
    return [key for key in world.objects.objects if key.startswith("payloads/")]


def untouched(world) -> tuple:
    """What a key conflict must leave as it was, and the lookup that answered it: (lookups,
    admissions, staged payloads, bound refs). R91: the 409 comes from the lookup, before
    anything is fetched, staged or admitted (review ADM-R2-B2)."""
    return (world.failures.count("lookup"), world.failures.count("admit"),
            staged_payloads(world), {job: list(refs) for job, refs in world.media.by_job.items()})


def looked_up(before: tuple) -> tuple:
    """`untouched` after one lookup and nothing else."""
    return (before[0] + 1, *before[1:])


def test_dur_admit__a_terminal_async_replay_is_answered_by_lookup_without_fetching():
    """R91 on the 202 path: a video job settled, its 202 long lost; by the retry the media host
    refuses the URL. The retry is answered from the store's lookup - 202, replayed, the
    committed `succeeded` - and nothing is fetched, staged or admitted again."""
    world = JobsWorld()
    first = post(world, payload=rs.body(rs.VIDEO), key="clip-1")
    assert first.status == 202
    rs.run(world.work())
    job = world.only_job()
    staged = staged_payloads(world)
    world.media.fetcher.transport = httpx.MockTransport(lambda request: httpx.Response(403))
    again = post(world, payload=rs.body(rs.VIDEO), key="clip-1")
    assert again.status == 202, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    body = again.json()
    assert (body["job_handle"], body["state"], body["idempotency_replayed"]) == (
        first.json()["job_handle"], "succeeded", True)
    assert staged_payloads(world) == staged and list(world.jobs.jobs) == [job.id]
    assert world.failures.count("admit") == 1


def refusing_host(fetched: list):
    """The customer's media host once the URL expired: every fetch is counted and refused."""
    def serve(request):
        fetched.append(request.url)
        return httpx.Response(403)
    return httpx.MockTransport(serve)


def test_dur_admit__an_in_flight_async_replay_prepares_nothing_when_the_host_fails():
    """Review ADM-R2-B1 (R91, G2 round 3): a video job accepted by `POST /v1/jobs` is running
    when its 202 is retried under the key, and by then the media host refuses the URL. The
    retry is 202, replayed, `running` - never a 400 for a live job - with nothing fetched or
    staged and the bound refs left as they are; the job runs on and settles once."""
    world = JobsWorld()
    assert post(world, payload=rs.body(rs.VIDEO), key="clip-5").status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    bound, staged, fetched = world.media.by_job[job.id], staged_payloads(world), []
    world.media.fetcher.transport = refusing_host(fetched)
    again = post(world, payload=rs.body(rs.VIDEO), key="clip-5")
    assert again.status == 202, again.body
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert (again.json()["job_handle"], again.json()["state"]) == (world.handle(), "running")
    assert fetched == [] and staged_payloads(world) == staged
    assert world.media.by_job[job.id] == bound and world.failures.count("admit") == 1
    assert job.state is JobState.running and not job.terminal
    rs.run(world.complete(lease))
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled


def test_dur_admit__an_in_flight_credit_replay_is_never_rechecked_or_cancelled():
    """Review ADM-R2-B1 (G2 round 3's money-B2 on the 202 path): a CREDIT job is running with
    committed output when the deployment's approved card rotates; the same-key `POST /v1/jobs`
    is 202, replayed, the same handle - not rechecked against the new card, never cancelled.
    Nothing is staged or admitted again, and the job runs on to its settlement."""
    world = JobsWorld(regime=CREDIT)
    assert post(world, key="credit-5").status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two ", "people"))
    staged, cancels = staged_payloads(world), world.failures.count("cancel")
    world.relay.active_rate_card_version = "rc_rotated_since"
    again = post(world, key="credit-5")
    assert again.status == 202, again.body
    assert (again.json()["job_handle"], again.json()["idempotency_replayed"]) == (
        world.handle(), True)
    assert job.state is JobState.running and not job.terminal and not world.released(job)
    assert world.failures.count("cancel") == cancels and world.failures.count("admit") == 1
    assert staged_payloads(world) == staged

    async def settles():
        ref = await world.put_result(job.id, "Two people")
        await world.jobs.complete_credit(lease, b.outcome(job.id, world, tokens=Usage.of(1200, 5),
                                                          result_ref=ref))

    rs.run(settles())
    assert job.outcome.state is JobState.succeeded and world.released(job)


def test_dur_admit__a_crash_after_the_admission_commit_is_completed_by_the_async_retry():
    """DUR-ADMIT, kill after commit and before the ack: the admission committed, the process
    died before the staged refs were bound and before any 202. The same-key retry finds the
    job in flight (R91 lookup), completes its acceptance (the idempotent attach) and answers
    202 for that job - which then runs to its end, one job and one hold."""
    world = JobsWorld()
    world.failures.crash_after_commit("admit")
    first = post(world, payload=rs.body(rs.VIDEO), key="clip-9")
    assert refusal(first) == (503, "dependency_unavailable")
    job = world.only_job()
    assert job.outcome is None and job.id not in world.media.by_job
    again = post(world, payload=rs.body(rs.VIDEO), key="clip-9")
    assert again.status == 202, again.body
    assert again.json()["job_handle"] == job.admission.job_handle
    assert again.json()["idempotency_replayed"] is True
    assert len(world.media.by_job.get(job.id, ())) == 1
    rs.run(world.work())
    assert list(world.jobs.jobs) == [job.id] and list(world.jobs.holds) == [job.id]
    assert job.outcome.state is JobState.succeeded


def test_dur_admit__a_store_outage_answering_a_replay_leaves_the_job_for_the_retry():
    """The retry's read of the job fails for want of the database: a retryable 503 with
    `Retry-After` (the driver's text in no answer), nothing cancelled or admitted, and the
    next retry answers the same job."""
    world = JobsWorld()
    assert post(world, key="order-10").status == 202
    job = world.only_job()
    world.failures.fail("get_owned", error=ConnectionError(
        "connection to postgresql://infrx:secret@db:5432 refused"))
    down = post(world, key="order-10")
    assert refusal(down) == (503, "dependency_unavailable")
    assert down.headers.get(wire.HEADER_RETRY_AFTER) and b"secret" not in down.body
    assert list(world.jobs.jobs) == [job.id] and not job.terminal
    assert world.jobs.holds[job.id].state is HoldState.held
    again = post(world, key="order-10")
    assert again.status == 202 and again.json()["job_handle"] == job.admission.job_handle

def test_dur_admit__a_changed_payload_under_the_key_is_409_and_admits_nothing():
    """Same org, operation and key, another canonical payload: 409 `idempotency_conflict`,
    and the store holds exactly the first job and its one hold."""
    world = JobsWorld()
    first = post(world, key="order-8")
    assert first.status == 202
    before = untouched(world)
    changed = post(world, payload=rs.body(temperature=0.5), key="order-8")
    assert refusal(changed) == (409, "idempotency_conflict")
    assert untouched(world) == looked_up(before)
    job = world.only_job()
    assert list(world.jobs.holds) == [job.id]
    assert world.jobs.wallet(world.org).reserved_total == world.jobs.holds[job.id].amount


def test_dur_admit__an_expired_mapping_is_410_and_never_a_new_billable_job():
    """01: an expired replay must not silently submit a new billable job. 24 h after the job
    ended its mapping is a tombstone: the retry is 410 `idempotency_expired`, and the store
    still holds one job, one settled hold and one debit."""
    world = JobsWorld()
    assert post(world, key="order-9").status == 202
    rs.run(world.work())
    job = world.only_job()
    ledger = world.jobs.wallet(world.org).ledger_total
    world.clock.advance(world.limits.idempotency_ttl_s)
    late = post(world, key="order-9")
    assert refusal(late) == (410, "idempotency_expired")
    assert list(world.jobs.jobs) == [job.id] and list(world.jobs.holds) == [job.id]
    assert world.jobs.wallet(world.org).ledger_total == ledger
    assert world.jobs.wallet(world.org).reserved_total == 0


def test_dur_admit__two_concurrent_submissions_with_one_key_admit_once():
    """Two `POST /v1/jobs` with one key at once, both past the R91 lookup before either is
    admitted (the lookup can miss a mapping being written): the admitting transaction is what
    decides - both are 202 for the same job, exactly one a replay, one job and one hold."""
    world = JobsWorld()
    lookup, missed, together = world.jobs.lookup, [], asyncio.Event()

    async def in_step(org_id, idem):
        found = await lookup(org_id, idem)
        missed.append(found)
        if len(missed) == 2:
            together.set()
        await together.wait()
        return found

    world.jobs.lookup = in_step

    async def both():
        return await asyncio.gather(send(world.app, "POST", JOBS, body=rs.body(), key="twice"),
                                    send(world.app, "POST", JOBS, body=rs.body(), key="twice"))

    replies = rs.run(both())
    assert missed == [None, None]
    assert [r.status for r in replies] == [202, 202]
    assert len(world.jobs.jobs) == 1
    job = world.only_job()
    assert {r.json()["job_handle"] for r in replies} == {job.admission.job_handle}
    assert sorted(r.json()["idempotency_replayed"] for r in replies) == [False, True]
    assert list(world.jobs.holds) == [job.id]
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.prepare_dispatch) == 1


def test_api_modes__a_detached_202_never_cancels_its_job():
    """A client that leaves as the 202 goes out does not cancel the job (G2's sync path does:
    a sync caller holds no identity; this one holds a handle). The job runs to its end."""
    world = JobsWorld()
    leave = asyncio.Event()

    def gone(message):
        if message["type"] == "http.response.start":
            leave.set()

    reply = post(world, leave=leave, on_send=gone)
    assert reply.status == 202
    job = world.only_job()
    assert not job.terminal and job.state is JobState.preparing
    rs.run(world.work())
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled


def test_api_modes__a_credit_async_job_is_admitted_on_its_wallet():
    """The 202 path admits by regime: in CREDIT the hold is on the consumer's CREDIT wallet
    (none on a USD wallet) and the deadline is the one the store kept - here the caller's
    bound, the gateway's clock running a minute behind the store's. A replay after the job
    ended answers its committed state (read with `get_owned_credit`)."""
    world = JobsWorld(regime=CREDIT)
    world.skew_s = -60.0
    reply = post(world, key="credit-1")
    assert reply.status == 202, reply.body
    job = world.only_job()
    assert job.credit is not None and not world.released(job)
    accepted = wire.JobAccepted.model_validate(reply.json())
    assert (accepted.job_handle, accepted.state) == (job.credit.job_handle, JobState.preparing)
    assert (accepted.created_at, accepted.deadline_at) == (job.credit.admitted_at,
                                                           job.admission.deadline_at)
    budgets = job.admission.budgets                  # the caller's bound is the earlier one
    assert accepted.deadline_at < job.credit.admitted_at + timedelta(
        seconds=budgets.preparation_s + budgets.queue_wait_s + budgets.generation_s)
    assert all(wallet.reserved_total == 0 for wallet in world.jobs.wallets.values())
    replays = []
    for _ in range(2):                       # retries at two later instants: one answer
        world.clock.advance(30)
        replays.append(wire.JobAccepted.model_validate(post(world, key="credit-1").json()))
    assert replays[0].deadline_at == replays[1].deadline_at == job.credit.admitted_at \
        + timedelta(seconds=budgets.preparation_s + budgets.queue_wait_s + budgets.generation_s)
    assert replays[0].deadline_at >= job.admission.deadline_at     # the ceiling, never earlier
    assert rs.run(send(world.app, "DELETE", job_path(world.handle()))).status == 200
    again = post(world, key="credit-1")
    assert again.status == 202 and again.json()["idempotency_replayed"] is True
    assert again.json()["state"] == "cancelled" and world.released(job)


def test_api_modes__plain_chat_is_never_a_surprise_202():
    """With the jobs router mounted, chat stays synchronous unless `respond-async` is asked
    for: no `Prefer`, or another preference, is answered 200 after the terminal commit, and a
    stream is a stream."""
    for headers in ({}, {"prefer": "wait=10"}):
        world = JobsWorld()
        world.during.append(world.work)
        reply = post(world, CHAT, headers=headers)
        assert reply.status == 200, (headers, reply.status, reply.body)
        assert wire.HEADER_PREFERENCE_APPLIED not in reply.headers
        job = world.only_job()
        assert job.request.execution_mode is ExecutionMode.sync
        assert job.outcome.state is JobState.succeeded
    world = JobsWorld()
    world.during.append(world.work)
    streamed = post(world, CHAT, rs.body(stream=True))
    assert streamed.status == 200 and streamed.data()[-1] == "[DONE]"

def test_dur_admit__a_key_reused_across_modes_is_409_and_the_job_runs_on():
    """R94: an idempotency key names one execution mode. A synchronous retry of an async job's
    key, whose client would then leave, is 409 `idempotency_conflict`: no wait is attached, and
    the async job runs on to its own end - one job, one hold. The reverse, an async replay of
    a synchronous job's key, is 409 too. The same mode still replays (chat with `Prefer`
    under a `POST /v1/jobs` key)."""
    world = JobsWorld()
    assert post(world, key="k-async").status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    leave = asyncio.Event()
    world.during.append(leave.set)            # were a wait attached, its client would leave
    before = untouched(world)
    sync_retry = post(world, CHAT, key="k-async", leave=leave)
    assert refusal(sync_retry) == (409, "idempotency_conflict")
    assert untouched(world) == looked_up(before)      # before any store write
    assert job.state is JobState.running and not job.terminal
    assert list(world.jobs.jobs) == [job.id] and list(world.jobs.holds) == [job.id]
    same_mode = post(world, CHAT, key="k-async", headers=PREFER)
    assert same_mode.status == 202
    assert same_mode.json()["job_handle"] == job.admission.job_handle
    rs.run(world.complete(lease))
    assert job.outcome.cause is TerminalCause.completed

    synchronous = JobsWorld()
    synchronous.during.append(synchronous.work)
    assert post(synchronous, CHAT, key="k-sync").status == 200
    before = untouched(synchronous)
    async_replay = post(synchronous, key="k-sync")
    assert refusal(async_replay) == (409, "idempotency_conflict")
    assert untouched(synchronous) == looked_up(before)
    assert len(synchronous.jobs.jobs) == 1

def test_dur_admit__the_key_names_the_payload_for_sync_and_stream_and_folds_only_async():
    """R94's identity, byte for byte (review ADM-R2-B3): the idempotency hash the acceptor
    receives is the payload digest itself for sync and for stream - their identity is
    unchanged, so a mapping stored before R94 still replays after it - and the folded digest
    for async, the same one on chat with `Prefer` and on `POST /v1/jobs`. The mode is the
    validated one, never the raw header: `Prefer: x-no-respond-async` is sync (ADM-R2-N1)."""
    world = JobsWorld()
    seen = []

    async def captured(auth, request, idem):
        seen.append((request.execution_mode, request.payload_digest, idem.payload_hash))
        raise errors.InvalidRequest("captured")          # nothing durable: the digest only

    world.relay.admit = captured
    for path, payload, headers in ((CHAT, rs.body(), {}), (CHAT, rs.body(stream=True), {}),
                                   (CHAT, rs.body(), PREFER), (JOBS, rs.body(), {}),
                                   (CHAT, rs.body(), {"prefer": "x-no-respond-async"})):
        assert refusal(post(world, path, payload, key="k", headers=headers)) == (
            400, "invalid_request")
    sync, stream, prefer, jobs, lookalike = seen
    assert [mode for mode, *_ in seen] == [ExecutionMode.sync, ExecutionMode.stream,
                                           ExecutionMode.async_, ExecutionMode.async_,
                                           ExecutionMode.sync]
    assert lookalike == sync, "the digest follows the validated mode, not the raw Prefer"
    assert sync[2] == sync[1], "a sync key's identity is its payload digest"
    assert stream[2] == stream[1] != sync[1], "a stream key's identity is its payload digest"
    assert sync[1] == prefer[1] == jobs[1] and prefer[2] == jobs[2] != prefer[1]
    assert not world.jobs.jobs and not staged_payloads(world)


# --- item 2: status --------------------------------------------------------------------
def status(world, handle=None, **kw):
    return get(world, job_path(handle or world.handle()), **kw)


def test_api_modes__status_reports_the_committed_row_and_result_availability():
    """`JobStatus` from the committed row: admitted, then running, then succeeded with the
    committed cause, settlement instant, authoritative usage and a result available until
    `result_ttl_s` after settlement (job_status.json's fields)."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    first = status(world)
    assert first.status == 200 and first.headers.get(wire.HEADER_INFERENCE_ID) == job.id
    assert first.json() == {"job_handle": job.admission.job_handle, "request_id": job.id,
                            "state": "preparing", "result_available": False,
                            "created_at": "2026-09-20T12:00:00Z",
                            "updated_at": "2026-09-20T12:00:00Z"}
    lease = rs.run(world.lease())
    assert status(world).json()["state"] == "running"
    world.clock.advance(9)
    rs.run(world.complete(lease))
    done = wire.JobStatus.model_validate(status(world).json())
    assert set(status(world).json()) == set(fixtures.load("job_status.json"))
    outcome = job.outcome
    assert (done.state, done.cause, done.result_available) == (JobState.succeeded,
                                                               outcome.cause, True)
    assert (done.created_at, done.updated_at) == (job.admission.admitted_at, outcome.settled_at)
    assert done.updated_at > done.created_at
    assert done.result_expires_at == outcome.settled_at + timedelta(
        seconds=world.limits.result_ttl_s)
    assert done.usage == wire.ChatUsage.of(outcome.usage)
    assert done.usage_certainty == "authoritative"


def test_api_modes__status_outlives_the_result_and_the_journal():
    """Status stays readable after the result expired and after the journal expired (01:
    metadata policy): `result_available` false and no expiry instant, while the result is 410
    `result_expired` and the events 410 `journal_expired`."""
    world = JobsWorld()
    assert post(world).status == 202
    rs.run(world.work())
    job = world.only_job()
    world.clock.advance(world.limits.result_ttl_s)
    after_result = status(world)
    assert after_result.status == 200
    body = after_result.json()
    assert (body["state"], body["result_available"]) == ("succeeded", False)
    assert "result_expires_at" not in body and body["usage_certainty"] == "authoritative"
    assert refusal(get(world, job_path(world.handle(), "/result"))) == (410, "result_expired")
    # The F fakes follow-up (a2779d1) replaced the fake's `expired_jobs` flag with D4's
    # definition (a prune watermark and no chunk left); the witness is the 410 below.
    assert rs.run(world.stream.expire()) > 0
    after_journal = status(world)
    assert after_journal.status == 200 and after_journal.json() == body
    assert refusal(get(world, job_path(world.handle(), "/events"))) == (410, "journal_expired")


def test_api_modes__a_success_without_usage_reports_none_and_no_result():
    """Published output whose completion carried no usage settles `held_unknown`: the status
    reports `usage_certainty: unknown` and no usage, and there is no chat result to serve
    (its shape requires usage, and none is invented) - the result is the outcome alone."""
    world = JobsWorld()
    assert post(world).status == 202
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    ref = rs.run(world.put_result(lease.job_id, "Two people"))
    rs.run(world.jobs.complete(lease, TerminalOutcome(
        job_id=lease.job_id, state=JobState.succeeded, cause=TerminalCause.completed,
        usage=None, result_ref=ref, settlement_state=SettlementState.released_free,
        settled_at=world.clock.now())))
    job = world.only_job()
    assert job.outcome.settlement_state is SettlementState.held_unknown
    body = status(world).json()
    assert (body["state"], body["usage_certainty"], body["result_available"]) == (
        "succeeded", "unknown", False)
    assert "usage" not in body and "result_expires_at" not in body
    result = get(world, job_path(world.handle(), "/result"))
    assert result.status == 200
    assert "response" not in result.json() and "usage" not in result.json()


def test_dur_rls__a_malformed_unknown_or_foreign_handle_is_one_404():
    """Every handle route: a malformed handle (refused before any store read), a handle one
    character off, and another tenant's real handle get byte-identical 404s, and the job is
    untouched - still admitted, its hold held, nothing cancelled or relayed."""
    world = JobsWorld()
    world.new_request_id = lambda: FIXED_ID
    world.restart()
    assert post(world).status == 202
    job = world.only_job()
    handle = world.handle()
    unknown = handle[:-1] + ("x" if handle[-1] != "x" else "y")
    world.as_key(OTHER_ROW)                                        # another organization
    journal_reads = world.failures.count("read_owned")
    for method, tail in (("GET", ""), ("GET", "/result"), ("GET", "/events"), ("DELETE", "")):
        reads = world.failures.count("get_owned")
        malformed = rs.run(send(world.app, method, job_path("job_short", tail)))
        assert world.failures.count("get_owned") == reads, "a malformed handle was read"
        answers = [malformed] + [rs.run(send(world.app, method, job_path(name, tail)))
                                 for name in (unknown, handle)]
        assert refusal(answers[0]) == (404, "not_found"), (method, tail, answers[0].body)
        assert all(a.messages == answers[0].messages for a in answers), (method, tail)
    assert not job.terminal and job.state is JobState.preparing
    assert world.jobs.holds[job.id].state is HoldState.held
    assert world.failures.count("read_owned") == journal_reads     # no journal was read


def test_dur_rls__a_provider_dev_key_owns_its_own_jobs():
    """Brief item 2: a provider-dev key of the organization owns the jobs it submits (to its
    private endpoint): its own handle's status and DELETE answer 200, and a consumer key of
    another organization gets the unknown handle's 404 for it."""
    world = JobsWorld()
    world.catalog.publish(rs.support.preview_card())
    world.jobs.set_price(rs.support.DEV_MODEL, b.price(model_revision=rs.support.DEV_MODEL))
    world.jobs.grant(rs.support.IDS.provider_org, "100")
    world.as_key(rs.support.PROVIDER_ROW)
    accepted = post(world, payload=rs.body(model=rs.support.DEV_MODEL))
    assert accepted.status == 202, accepted.body
    job = world.only_job()
    assert job.request.org_id == rs.support.IDS.provider_org
    own = status(world)
    assert own.status == 200 and own.json().get("state") == "preparing"
    world.as_key(OTHER_ROW)
    assert refusal(status(world)) == (404, "not_found")
    world.as_key(rs.support.PROVIDER_ROW)
    cancelled = delete(world)
    assert cancelled.status == 200 and cancelled.json()["state"] == "cancelled"
    assert job.outcome.cause is TerminalCause.client_cancelled

def test_api_modes__a_store_outage_on_a_handle_read_is_a_retryable_503():
    """Every handle route's store read - the owned row (status, result, events, DELETE), the
    events' pre-header journal probe, the store clock (status, result, DELETE) and the
    committed result object (result) - that fails for want of the database is a retryable 503
    with `Retry-After`, the driver's text in no answer, and nothing is changed (review S4,
    stream-C1: the clock and the result object read a succeeded job)."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    outage = ConnectionError("connection to postgresql://infrx:secret@db:5432 refused")
    for operation, method, tail in (("get_owned", "GET", ""), ("get_owned", "GET", "/result"),
                                    ("get_owned", "GET", "/events"), ("get_owned", "DELETE", ""),
                                    ("read_owned", "GET", "/events")):
        world.failures.fail(operation, on_call=world.failures.count(operation) + 1,
                            error=outage)
        reply = rs.run(send(world.app, method, job_path(world.handle(), tail)))
        assert refusal(reply) == (503, "dependency_unavailable"), (operation, method, tail)
        assert reply.headers.get(wire.HEADER_RETRY_AFTER) and b"secret" not in reply.body
    assert not job.terminal and world.jobs.holds[job.id].state is HoldState.held
    assert status(world).status == 200
    rs.run(world.work())
    db_now, read_result = world.jobs.db_now, world.read_result

    async def clock_down():
        raise outage

    async def object_down(org_id, ref):
        raise OSError("GET https://infrx:secret@objects.internal/results refused")

    for down, method, tail in (("db_now", "GET", ""), ("db_now", "GET", "/result"),
                               ("db_now", "DELETE", ""), ("read_result", "GET", "/result")):
        world.jobs.db_now, world.read_result = db_now, read_result
        if down == "db_now":
            world.jobs.db_now = clock_down
        else:
            world.read_result = object_down
        reply = rs.run(send(world.app, method, job_path(world.handle(), tail)))
        assert refusal(reply) == (503, "dependency_unavailable"), (down, method, tail)
        assert reply.headers.get(wire.HEADER_RETRY_AFTER) and b"secret" not in reply.body
    world.jobs.db_now, world.read_result = db_now, read_result
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled
    assert result(world).json()["response"]["choices"][0]["message"]["content"] \
        == world.results[job.id]

def test_dur_rls__an_operator_key_owns_no_job():
    """R33 / R66: an operator credential of the very organization owns no job - every handle
    route answers 404, and nothing is read out or cancelled."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    handle = world.handle()
    world.as_key(rs.support.OPERATOR_ROW)
    assert rs.support.OPERATOR_ROW["org_id"] == world.org
    for method, tail in (("GET", ""), ("GET", "/result"), ("GET", "/events"), ("DELETE", "")):
        reply = rs.run(send(world.app, method, job_path(handle, tail)))
        assert refusal(reply) == (404, "not_found"), (method, tail, reply.body)
    assert not job.terminal and world.jobs.holds[job.id].state is HoldState.held


# --- item 3: result --------------------------------------------------------------------
def result(world, handle=None, **kw):
    return get(world, job_path(handle or world.handle(), "/result"), **kw)


def test_api_modes__the_result_is_served_only_after_the_terminal_commit():
    """While the job runs - even with output published - the result is 409 `result_pending`.
    After the settling commit it is `JobResult` (job_result.json's fields): the committed
    result object in the chat shape, the settled usage, `completed_at` the settlement."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    assert refusal(result(world)) == (409, "result_pending")
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    assert refusal(result(world)) == (409, "result_pending")
    world.clock.advance(9)
    rs.run(world.complete(lease))
    reply = result(world)
    assert reply.status == 200 and reply.headers.get(wire.HEADER_INFERENCE_ID) == job.id
    body = reply.json()
    assert set(body) == set(fixtures.load("job_result.json"))
    served = wire.JobResult.model_validate(body)
    outcome = job.outcome
    assert (served.state, served.cause, served.completed_at) == (
        JobState.succeeded, outcome.cause, outcome.settled_at)
    assert served.usage == served.response.usage == wire.ChatUsage.of(outcome.usage)
    response = served.response
    assert response.choices[0].message.content == world.results[job.id]
    assert (response.id, response.model, response.created) == (
        f"chatcmpl-{job.id}", job.request.model_revision,
        int(job.admission.admitted_at.timestamp()))


def test_api_modes__a_failed_cancelled_or_expired_job_is_a_result_not_an_error():
    """01: "failure" is a result. A failed, a cancelled and a queue-expired job each answer
    200 `JobResult` with the committed state and cause and no response - never an error
    envelope - and none is billed."""
    world = JobsWorld()
    handles = {name: post(world, key=name).json()["job_handle"]
               for name in ("failed", "cancelled", "expired")}
    ids = {name: world.jobs.by_handle[handle] for name, handle in handles.items()}

    async def run_all():
        await world.prepare(ids["failed"])
        await world.runner(world.upstream("engine_error_pre_headers")).run(ids["failed"])
        await world.prepare(ids["expired"])

    rs.run(run_all())
    assert rs.run(send(world.app, "DELETE", job_path(handles["cancelled"]))).status == 200
    world.clock.advance(world.limits.queue_wait_async_s + 1)
    rs.run(world.jobs.recover())
    expected = {"failed": ("failed", "engine_error"), "cancelled": ("cancelled",
                                                                    "client_cancelled"),
                "expired": ("expired", "queue_wait_expired")}
    for name, handle in handles.items():
        reply = result(world, handle)
        assert reply.status == 200, (name, reply.body)
        body = reply.json()
        assert (body["state"], body["cause"]) == expected[name], name
        assert "response" not in body and "error" not in body, name
        job = world.jobs.jobs[ids[name]]
        assert wire.JobResult.model_validate(body).completed_at == job.outcome.settled_at
        assert job.outcome.debit == 0 and world.jobs.holds[job.id].state is HoldState.released


def test_api_modes__result_expiry_is_judged_on_the_store_clock():
    """R29/R79: with the gateway's clock an hour ahead - past the result's TTL by its own
    reckoning - the result is still served, because the store's clock is not past it. Once the
    store's clock passes `settled_at + result_ttl_s` the result is 410 `result_expired`."""
    world = JobsWorld()
    assert post(world).status == 202
    rs.run(world.work())
    job = world.only_job()
    world.clock.advance(world.limits.result_ttl_s - 1_800)
    world.skew_s = 3_600.0
    assert world.relay._now() > job.outcome.settled_at + timedelta(
        seconds=world.limits.result_ttl_s)
    served = result(world)
    assert served.status == 200 and served.json()["response"]["choices"]
    assert status(world).json()["result_available"] is True
    world.clock.advance(1_801)
    assert refusal(result(world)) == (410, "result_expired")
    assert status(world).json()["result_available"] is False
    assert job.outcome.state is JobState.succeeded and world.jobs.holds[job.id].state \
        is HoldState.settled


# --- item 4: events replay -------------------------------------------------------------
def events(world, handle=None, cursor=None, **kw):
    headers = {wire.HEADER_LAST_EVENT_ID: cursor} if cursor is not None else {}
    return get(world, job_path(handle or world.handle(), "/events"), headers=headers, **kw)


def ids_of(reply) -> list:
    return [line[len("id: "):] for frame in reply.frames for line in frame.split("\n")
            if line.startswith("id: ")]


def test_api_modes__events_replay_the_committed_journal_from_the_cursor():
    """A job already terminal replays its committed journal and ends: the identity frame (no
    id), `visible` text only (never the reasoning in `raw`), the settled usage, `[DONE]` at
    the terminal event's cursor. From a `Last-Event-ID` only what follows it is sent. Reading
    changes nothing in the store."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.stream.append(lease, (EngineEvent(type=ChunkEventType.delta, payload={
        "visible": "Two people", "raw": "<think>the secret plan</think>Two people"}),)))
    rs.run(world.commit(lease, " unload boxes."))
    rs.run(world.complete(lease))
    whole = events(world)
    assert whole.status == 200 and whole.headers.get(wire.HEADER_INFERENCE_ID) == job.id
    assert whole.headers["content-type"].startswith("text/event-stream")
    assert whole.events()[0] == "infrx.progress" and not whole.frames[0].startswith("id:")
    assert whole.data()[0] == {"job_handle": world.handle(), "request_id": job.id,
                               "phase": "succeeded"}
    assert whole.text() == "Two people unload boxes." and b"secret" not in whole.body
    usage = whole.data()[-2].get("usage")
    assert usage == wire.ChatUsage.of(job.outcome.usage).model_dump()
    (terminal,) = [c for c in world.journal(job.id) if c.event_type is ChunkEventType.terminal]
    assert whole.frames[-1] == f"id: {terminal.cursor.token}\ndata: [DONE]"
    first_delta = [c for c in world.journal(job.id) if c.event_type is ChunkEventType.delta][0]
    resumed = events(world, cursor=first_delta.cursor.token)
    assert resumed.status == 200 and resumed.text() == " unload boxes."
    assert first_delta.cursor.token not in ids_of(resumed)
    assert resumed.frames[-1] == whole.frames[-1]
    assert job.outcome.state is JobState.succeeded and job.outcome.cause.value == "completed"


def test_api_modes__a_malformed_or_forged_cursor_is_400_before_any_read():
    """R36: cursors are opaque. A malformed `Last-Event-ID` is 400 `invalid_cursor` before any
    store read; one the journal never issued is the store's 400, answered before any SSE
    header. The running job is untouched."""
    world = JobsWorld()
    assert post(world).status == 202
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    world.during.append(lambda: world.complete(lease))       # a stream that started would end
    reads = world.failures.count("get_owned"), world.failures.count("read_owned")
    malformed = events(world, cursor="abc")
    assert refusal(malformed) == (400, "invalid_cursor")
    assert (world.failures.count("get_owned"), world.failures.count("read_owned")) == reads
    forged = events(world, cursor="7-99")
    assert refusal(forged) == (400, "invalid_cursor")
    assert forged.headers["content-type"] == "application/json"
    job = world.only_job()
    assert job.state is JobState.running and not job.terminal


def test_api_modes__a_replay_gap_or_an_expired_journal_is_an_explicit_410():
    """Never silent: replay from before what the journal still holds is 410 `replay_gap`
    (from a retained cursor it replays), and once the journal expired it is 410
    `journal_expired`. Both before any SSE header; the job is untouched."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two", " people", " unload boxes."))
    rs.run(world.complete(lease))
    kept = world.journal(job.id)[2:]                 # what `expire` leaves after a partial prune
    world.stream.chunks[job.id] = kept
    world.stream.pruned_to[job.id] = (1, 2)
    gap = events(world)
    assert refusal(gap) == (410, "replay_gap")
    assert gap.headers["content-type"] == "application/json"
    resumed = events(world, cursor="1-2")
    assert resumed.status == 200 and resumed.text() == " unload boxes."
    world.clock.advance(world.limits.journal_chunk_ttl_s)
    assert rs.run(world.stream.expire()) == len(kept)
    assert refusal(events(world, cursor="1-2")) == (410, "journal_expired")
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled


def test_api_modes__an_observer_that_leaves_never_cancels_the_job():
    """01: an async event-observer disconnect detaches only. The observer leaves mid-stream;
    the job is still running afterwards and its worker completes it - settled once."""
    world = JobsWorld()
    assert post(world).status == 202
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    leave = asyncio.Event()
    world.during.append(leave.set)
    reply = events(world, leave=leave)
    assert reply.status == 200 and reply.text() == "Two people"
    job = world.only_job()
    assert job.state is JobState.running and not job.terminal
    rs.run(world.commit(lease, " unload boxes."))
    rs.run(world.complete(lease))
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled


def test_api_modes__a_credit_job_replays_its_events_in_its_committed_phase():
    """CREDIT, where the admission carries no lifecycle state: the events of a terminal job
    name the committed outcome's state in the identity frame (not the admitted `preparing`),
    replay the committed output, and end on the outcome - an error frame and `[DONE]`."""
    world = JobsWorld(regime=CREDIT)
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    assert delete(world).json()["state"] == "cancelled"
    reply = events(world)
    assert reply.status == 200
    assert reply.data()[0] == {"job_handle": world.handle(), "request_id": job.id,
                               "phase": "cancelled"}
    assert reply.text() == "Two people" and reply.data()[-1] == "[DONE]"
    assert "infrx.error" in reply.events()

# The observer's sends, in order: the headers, the identity frame, the first committed delta.
FAILING_SEND = {"start": 1, "identity": 2, "first_delta": 3}


@pytest.mark.parametrize("failing", sorted(FAILING_SEND))
def test_api_modes__an_observer_whose_stream_fails_never_cancels_the_job(failing):
    """The other ways an observer ends: a send fails (a peer gone without a disconnect
    message) - on the headers, on the identity frame or on the first delta (review
    stream-C4) - or a prune lands between the pre-header probe and the pump's first read (a
    `replay_gap` frame after the headers). None cancels: no store cancel is issued, the job is
    still running and its worker completes it."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    sent, cancels = [], world.failures.count("cancel")

    def peer_gone(message):
        sent.append(message)
        if len(sent) == FAILING_SEND[failing]:
            raise OSError("connection reset by peer")

    failed = events(world, on_send=peer_gone)
    assert failed.status == 200 and len(sent) > FAILING_SEND[failing]
    assert world.failures.count("cancel") == cancels
    assert job.state is JobState.running and not job.terminal
    world.failures.fail("read_owned", on_call=world.failures.count("read_owned") + 2,
                        error=errors.ReplayGap("pruned between the probe and the pump"))
    gap = events(world)
    assert gap.status == 200
    assert [item["error"]["code"] for item in gap.data()
            if isinstance(item, dict) and "error" in item] == ["replay_gap"]
    assert job.state is JobState.running and not job.terminal
    rs.run(world.complete(lease))
    assert job.outcome.cause is TerminalCause.completed


def test_api_modes__an_observer_stopped_from_outside_never_cancels_the_job():
    """The observer's task is cancelled while the pump waits (the process stopping): the
    cancellation propagates, no cancel is issued, and the job runs on."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    world.during.append(lambda: asyncio.current_task().cancel())

    async def observe():
        with pytest.raises(asyncio.CancelledError):
            await send(world.app, "GET", job_path(world.handle(), "/events"))
        await asyncio.gather(*world.relay._cancels)

    rs.run(observe())
    assert job.state is JobState.running and not job.terminal
    rs.run(world.complete(lease))
    assert job.outcome.cause is TerminalCause.completed

def test_api_modes__an_unstarted_job_streams_its_identity_then_waits():
    """A job with nothing committed yet: the identity frame (its admitted phase) first, then
    keepalive comments while it waits, then the committed output as the worker produces it,
    ended by `[DONE]` at the terminal commit."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    world.during += [lambda: world.clock.advance(world.limits.sse_keepalive_s + 1), world.work]
    reply = events(world)
    assert reply.status == 200
    assert reply.data()[0] == {"job_handle": world.handle(), "request_id": job.id,
                               "phase": "preparing"}
    kinds = reply.events()
    assert kinds[0] == "infrx.progress" and "comment" in kinds
    assert kinds.index("comment") < [i for i, f in enumerate(reply.frames)
                                     if "chat.completion.chunk" in f][0]
    assert reply.text() == world.results[job.id] and reply.data()[-1] == "[DONE]"
    assert job.outcome.state is JobState.succeeded


# --- item 5: DELETE --------------------------------------------------------------------
def delete(world, handle=None, **kw):
    return rs.run(send(world.app, "DELETE", job_path(handle or world.handle()), **kw))


def test_dur_fence__delete_cancels_durably_and_answers_the_committed_outcome():
    """An explicit DELETE cancels in the store with the client's cause (R21: an explicit
    cancel is `client_cancelled`, never `client_disconnected`) and answers the committed
    `JobStatus`; the worker's next fenced write is refused. A second DELETE answers the same
    committed outcome - no conflict, no second settlement."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    first = delete(world)
    assert first.status == 200, first.body
    body = first.json()
    assert (body["state"], body.get("cause"), body["result_available"]) == (
        "cancelled", "client_cancelled", False)
    assert set(body) <= set(fixtures.load("job_status.json")) | {"usage_certainty"}
    outcome = job.outcome
    assert (job.state, outcome.cause) == (JobState.cancelled, TerminalCause.client_cancelled)
    assert outcome.settlement_state is SettlementState.held_unknown and outcome.debit == 0
    with pytest.raises(errors.AlreadyTerminal):
        rs.run(world.commit(lease, " unload"))
    again = delete(world)
    assert again.status == 200 and again.json() == body
    assert job.outcome is outcome
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1


@pytest.mark.parametrize("first", ["completion", "delete"])
def test_dur_fence__a_delete_racing_a_completion_settles_once(first):
    """D3's serialization, both orders: whichever commits first is the job's one outcome.
    Completion first - DELETE answers `succeeded` (never an error) and the debit stands.
    DELETE first - the worker's completion is refused and nothing is billed. Either way one
    settlement and one usage projection."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    lease = rs.run(world.lease())
    rs.run(world.commit(lease, "Two people"))
    if first == "completion":
        rs.run(world.complete(lease))
        ledger = world.jobs.wallet(world.org).ledger_total
        reply = delete(world)
        assert reply.status == 200 and (reply.json()["state"], reply.json().get("cause")) == (
            "succeeded", "completed")
        assert job.outcome.settlement_state is SettlementState.settled and job.outcome.debit > 0
        assert world.jobs.wallet(world.org).ledger_total == ledger
    else:
        ledger = world.jobs.wallet(world.org).ledger_total
        reply = delete(world)
        assert reply.status == 200 and reply.json()["state"] == "cancelled"
        with pytest.raises(errors.AlreadyTerminal):
            rs.run(world.complete(lease))
        assert job.outcome.state is JobState.cancelled and job.outcome.debit == 0
        assert world.jobs.wallet(world.org).ledger_total == ledger
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1
    assert delete(world).json() == reply.json()


def test_dur_fence__a_delete_cancelled_midway_still_cancels_the_job():
    """The DELETE handler is cancelled (the client or the process goes) while the store's
    cancel is in flight: the cancel is shielded, so it still commits."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    handle = world.handle()
    release, cancels = asyncio.Event(), []
    cancel = world.jobs.cancel

    async def slow_cancel(org_id, job_handle, **cause):
        cancels.append(job_handle)
        await release.wait()
        return await cancel(org_id, job_handle, **cause)

    world.jobs.cancel = slow_cancel

    async def body():
        task = asyncio.ensure_future(send(world.app, "DELETE", job_path(handle)))
        for _ in range(200):                  # bounded: a DELETE that never cancels fails
            if cancels:
                break
            await asyncio.sleep(0)
        assert cancels, "the DELETE never reached the store"
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        await asyncio.gather(*world.relay._cancels)

    rs.run(body())
    assert job.state is JobState.cancelled
    assert job.outcome.cause is TerminalCause.client_cancelled


def test_dur_fence__a_delete_whose_cancel_fails_is_retryable_never_a_200():
    """The store's cancel fails for want of the database: the DELETE is a retryable 503 with
    `Retry-After` (the driver's text in no answer) - never a 200 showing the row before the
    cancel - the job is untouched, and the retried DELETE cancels it."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    world.failures.fail("cancel", error=ConnectionError(
        "connection to postgresql://infrx:secret@db:5432 refused"))
    down = delete(world)
    assert refusal(down) == (503, "dependency_unavailable")
    assert down.headers.get(wire.HEADER_RETRY_AFTER) and b"secret" not in down.body
    assert not job.terminal and world.jobs.holds[job.id].state is HoldState.held
    again = delete(world)
    assert again.status == 200 and again.json()["state"] == "cancelled"
    assert job.outcome.cause is TerminalCause.client_cancelled

def test_dur_fence__a_delete_after_completion_whose_outcome_read_fails_is_retryable():
    """PostgreSQL answers a DELETE of a job that already completed with `already_terminal`,
    and the relay then reads the committed outcome (D3). When that read fails for want of
    the database the DELETE is a retryable 503 - never a 200 showing the row without its
    outcome (review stream-C3) - and the retried DELETE answers `succeeded`: one settlement."""
    world = JobsWorld()
    assert post(world).status == 202
    rs.run(world.work())
    job = world.only_job()
    outcome, ledger = job.outcome, world.jobs.wallet(world.org).ledger_total

    async def already_terminal(org_id, job_handle, **cause):
        raise errors.AlreadyTerminal("the job is already terminal")

    world.jobs.cancel = already_terminal                     # PgJobStore's answer
    world.failures.fail("get_owned", on_call=world.failures.count("get_owned") + 2,
                        error=ConnectionError("connection to postgresql://infrx:secret@db:5432 "
                                              "refused"))     # the read after already_terminal
    down = delete(world)
    assert refusal(down) == (503, "dependency_unavailable")
    assert down.headers.get(wire.HEADER_RETRY_AFTER) and b"secret" not in down.body
    again = delete(world)
    assert again.status == 200
    assert (again.json()["state"], again.json().get("cause")) == ("succeeded", "completed")
    assert job.outcome is outcome and outcome.settlement_state is SettlementState.settled
    assert world.jobs.wallet(world.org).ledger_total == ledger
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1


def test_api_modes__a_delete_with_a_body_is_refused_and_cancels_nothing():
    """A DELETE carries no body: one that declares one (a length, or chunked) is 400, decided
    from the headers without reading it, and the job runs on."""
    world = JobsWorld()
    assert post(world).status == 202
    job = world.only_job()
    for headers in ({"content-length": "2"}, {"transfer-encoding": "chunked"}):
        reply = delete(world, body={}, headers=headers)
        assert refusal(reply) == (400, "invalid_request"), headers
    assert not job.terminal and world.jobs.holds[job.id].state is HoldState.held
    assert delete(world, headers={"content-length": "0"}).json()["state"] == "cancelled"


# --- item 6: the explicit-async matrix ---------------------------------------------------
INPUTS = {"text": rs.TEXT, "video_url": rs.VIDEO}


@pytest.mark.parametrize("route", ["post_jobs", "prefer"])
@pytest.mark.parametrize("kind", sorted(INPUTS))
def test_api_modes__the_async_matrix_end_to_end(kind, route):
    """Each cell, end to end through W's real attempt runner: 202, then either polling
    (`POST /v1/jobs`: status until terminal, then the result) or the events (`Prefer` on
    chat: the whole committed journal, then a resume from its first cursor), then the
    result. One settlement, the hold settled, capacity released."""
    world = JobsWorld()
    if route == "post_jobs":
        accepted = post(world, payload=rs.body(INPUTS[kind]))
    else:
        accepted = post(world, CHAT, rs.body(INPUTS[kind]), headers=PREFER)
    assert accepted.status == 202, accepted.body
    job = world.only_job()
    handle = accepted.json()["job_handle"]
    assert accepted.headers.get(jobs_router.HEADER_LOCATION) == job_path(handle)
    if route == "post_jobs":
        assert status(world, handle).json()["state"] == "preparing"
        rs.run(world.work())
        assert status(world, handle).json()["state"] == "succeeded"
    else:
        world.during.append(world.work)
        whole = events(world, handle)
        assert whole.text() == world.results[job.id] and whole.data()[-1] == "[DONE]"
        first = [c for c in world.journal(job.id) if c.event_type is ChunkEventType.delta][0]
        rest = events(world, handle, cursor=first.cursor.token)
        assert ids_of(rest) == ids_of(whole)[ids_of(whole).index(first.cursor.token) + 1:]
        assert rest.text() == whole.text()[len(first.payload["visible"]):]
        assert rest.frames[-1] == whole.frames[-1]
    answer = result(world, handle)
    assert answer.status == 200
    assert answer.json()["response"]["choices"][0]["message"]["content"] == world.results[job.id]
    outcome = job.outcome
    assert (outcome.state, outcome.settlement_state) == (JobState.succeeded,
                                                         SettlementState.settled)
    assert world.jobs.holds[job.id].state is HoldState.settled
    assert not any(r.active for r in job.reservations.values())
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1
    if kind == "video_url":
        (ref,) = world.media.prepared_by_job[job.id]
        assert ref.org_id == world.org and ref.duration_s == 4.0


def test_api_modes__a_job_that_expires_in_the_queue_is_an_expired_result():
    """A CREDIT job whose async queue wait ran out (`recover` on the store clock): its status
    is job_expired.json's shape - `expired` / `queue_wait_expired`, no result, no usage - its
    result is the outcome with no response (the result's own expiry is the 410, not this),
    and its hold is released unbilled."""
    world = JobsWorld(regime=CREDIT)
    assert post(world).status == 202
    job = world.only_job()
    assert status(world).json()["state"] == "preparing"
    rs.run(world.prepare())
    world.clock.advance(world.limits.queue_wait_async_s + 1)
    rs.run(world.jobs.recover())
    body = status(world).json()
    assert set(body) == set(fixtures.load("job_expired.json"))
    assert (body["state"], body["cause"], body["result_available"]) == (
        "expired", "queue_wait_expired", False)
    assert body["updated_at"] > body["created_at"]
    answer = result(world)
    assert answer.status == 200 and "response" not in answer.json()
    assert (answer.json()["state"], answer.json()["cause"]) == ("expired", "queue_wait_expired")
    assert world.released(job) and job.settlement is None


# --- item 7: the client example's explicit-async flow ----------------------------------
_spec = importlib.util.spec_from_file_location(
    "client_example", pathlib.Path(__file__).resolve().parents[3] / "client_example.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)


def test_api_modes__the_client_examples_async_flow_is_served(monkeypatch, capsys):
    """`client_example.py quickstart --respond-async` against the jobs routes, in process: the
    202, one wait of the 202's `Retry-After` (the worker runs meanwhile), the status, then the
    result - `done`, with the committed text and usage. The key comes from the environment
    and appears in no output."""
    world = JobsWorld()
    slept = []

    async def wait(seconds):
        slept.append(seconds)
        await world.work()

    monkeypatch.setattr(client, "SLEEP", wait)
    secret = service.new_secret()
    monkeypatch.setenv("INFRX_API_KEY", secret)
    monkeypatch.delenv("MARLIN_API_KEY", raising=False)
    code = client.main(["quickstart", "--base", "http://gateway.test/v1", "--video", rs.CLIP_URL,
                        "--respond-async"], transport=httpx.ASGITransport(app=world.app))
    out = capsys.readouterr().out
    row = json.loads(out)
    job = world.only_job()
    assert (code, row["status"], row["http_status"]) == (0, "done", 202), out
    assert slept == [float(jobs_router.POLL_AFTER_S)]
    assert row["content"] == world.results[job.id] and row["inference_id"] == job.id
    assert row["completion_tokens"] == job.outcome.usage.completion_tokens
    assert job.request.execution_mode is ExecutionMode.async_ and len(job.request.media) == 1
    assert job.outcome.settlement_state is SettlementState.settled and secret not in out


# --- item 8: composition and the route table ----------------------------------------------
def test_f_base__the_jobs_router_mounts_only_over_a_relay():
    """M-FAILCLOSED: without `rt.relay` the jobs router mounts nothing (there is no fake to
    fall back on) and the route table stays valid; over a relay it mounts the five routes,
    installs its 202 hook, and refuses to start without the ingress's dependencies."""
    app, _ = rs.support.cutover_app()
    rt = app.state.runtime
    rt.ingress = rs.support.deps()                    # the cutover's shape: ingress deps set
    assert jobs_router.register(app, rt) is None
    assert not [r for r in app.routes if getattr(r, "path", "").startswith(JOBS)]
    ingress.assert_route_table(app)
    world = JobsWorld()
    served = {(method, r.path) for r in world.app.routes if r.path.startswith(JOBS)
              for method in r.methods}
    assert served == set(ingress.JOBS_ROUTES)
    assert world.relay.on_async == world.router.accepted
    bare, _ = rs.support.cutover_app()
    bare.state.runtime.relay = world.relay
    with pytest.raises(RuntimeMisconfigured):
        jobs_router.register(bare, bare.state.runtime)             # no rt.ingress


def test_f_base__each_jobs_route_has_one_handler_and_it_is_the_jobs_routers():
    """The composition root's route-table assertion covers the jobs routes: all five or none,
    each served by exactly one handler, this module's. A second handler, or a partial mount,
    refuses to start."""
    world = JobsWorld()
    for method, path in ingress.JOBS_ROUTES:
        (route,) = [r for r in world.app.routes if r.path == path and method in r.methods]
        assert route.endpoint.__module__ == jobs_router.__name__
    ingress.assert_route_table(world.app)

    @world.app.post(JOBS)
    async def shadow():
        return {}

    with pytest.raises(RuntimeMisconfigured):
        ingress.assert_route_table(world.app)
    partial, _ = rs.support.cutover_app()

    @partial.get(jobs_router.JOB_PATH)
    async def lone():
        return {}

    lone.__module__ = jobs_router.__name__
    with pytest.raises(RuntimeMisconfigured):
        ingress.assert_route_table(partial)

    async def catch_all():
        return {}

    # A pattern route registered before the jobs router serves a jobs path without being "at"
    # it: a GET catch-all under /v1, and a DELETE-only one under /v1/jobs (review stream-C2).
    for pattern, method in (("/v1/{rest:path}", "GET"), (JOBS + "/{rest:path}", "DELETE")):
        shadowed, _ = rs.support.cutover_app()
        shadowed.add_api_route(pattern, catch_all, methods=[method])
        rt = shadowed.state.runtime
        rt.relay = world.relay
        rt.ingress = rs.support.deps(accept=world.relay.accept, catalog=world.catalog)
        jobs_router.register(shadowed, rt)
        with pytest.raises(RuntimeMisconfigured):
            ingress.assert_route_table(shadowed)


class Journal(FakeStreamStore):
    """The contract journal plus D4's `usage()`, the pilot's journal readiness probe."""

    async def usage(self):
        return {"reserved_bytes": 0, "stored_bytes": 0, "charged_bytes": 0, "chunks": 0}


def test_f_base__the_pilot_composition_carries_the_relay_the_jobs_router_needs():
    """The cutover's order over G2's `pilot.build_ingress_deps` (the fakes standing in for
    D4/D5/M): the composition puts its relay on `rt.relay`, so registering the ingress then the
    jobs router over `rt.ingress` mounts the jobs routes on the pilot's own relay, with its 202
    hook installed, and the route table holds. Integration request: `ROUTERS` gains `jobs`."""
    harness = credit_jobstore_factory()
    store, clock = harness.port, harness.clock
    store.catalog = catalog = rs.support.catalog()
    rt = rs.support.runtime(rs.support.settings(),
                            sb=rs.support.supabase(rows=(rs.CONSUMER_ROW,)),
                            clock=lambda: clock.now().timestamp())
    rt.ingress = pilot.build_ingress_deps(rt, catalog=catalog, stream=Journal(store),
                                          objects=InMemoryObjectStore(), jobs=store,
                                          index=MemoryScheduler(clock.now))
    app = FastAPI()
    app.state.runtime, rt.app = rt, app
    for module in (ingress, jobs_router):                          # ROUTERS' order
        module.register(app, rt)
    ingress.assert_route_table(app)
    assert rt.relay.on_async is not None and rt.relay.on_async.__self__.relay is rt.relay
    assert {(m, r.path) for r in app.routes if r.path.startswith(JOBS) for m in r.methods} \
        == set(ingress.JOBS_ROUTES)
