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
from datetime import timedelta

import pytest

from infrx.contracts import fixtures, wire
from infrx.contracts.records import (ExecutionMode, HoldState, JobState, OutboxKind,
                                     SettlementState, TerminalCause, TerminalOutcome)
from infrx.gateway.routes import jobs as jobs_router
from infrx.gateway.routes.relay import CREDIT
from infrx.observe.metrics import Registry

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
    """(status, error code) of a reply, read so that a reply that is not an error fails the
    comparison rather than raising."""
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
    rs.run(world.complete(lease))
    assert job.outcome.state is JobState.succeeded
    assert world.jobs.holds[job.id].state is HoldState.settled


def test_dur_admit__a_changed_payload_under_the_key_is_409_and_admits_nothing():
    """Same org, operation and key, another canonical payload: 409 `idempotency_conflict`,
    and the store holds exactly the first job and its one hold."""
    world = JobsWorld()
    first = post(world, key="order-8")
    assert first.status == 202
    changed = post(world, payload=rs.body(temperature=0.5), key="order-8")
    assert refusal(changed) == (409, "idempotency_conflict")
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
    """Two `POST /v1/jobs` with one key at once: both are 202 for the same job, exactly one
    of them a replay, and the store holds one job and one hold."""
    world = JobsWorld()

    async def both():
        return await asyncio.gather(send(world.app, "POST", JOBS, body=rs.body(), key="twice"),
                                    send(world.app, "POST", JOBS, body=rs.body(), key="twice"))

    replies = rs.run(both())
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
    assert rs.run(send(world.app, "DELETE", job_path(world.handle()))).status == 200
    again = post(world, key="credit-1")
    assert again.status == 202 and again.json()["idempotency_replayed"] is True
    assert again.json()["state"] == "cancelled" and world.released(job)


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
    assert rs.run(world.stream.expire()) > 0 and job.id in world.stream.expired_jobs
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
