#!/usr/bin/env python3
"""API-MODES / DUR-ADMIT (G half) / DUR-OUTPUT: G2's acceptor and synchronous wait.

    uv run --frozen pytest -q tests/g/test_relay_sync.py

Every case runs the cutover's app (the metered ingress with `Relay.accept`) against the
contract fakes and M's real media adapter, and asserts store state - job state, hold,
journal, staged payload - not only the HTTP answer. `World.during` is what the store does
while the gateway waits: one step per poll, so "between two polls" is exact.

Implemented on fakes: D2's `PgJobStore` and D4's `PgStreamStore` are the integration target.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from infrx.contracts import errors, wire
from infrx.contracts.records import HoldState, JobState, SettlementState, TerminalCause
from infrx.gateway.routes.relay import CREDIT
from infrx.observe.metrics import Registry, tenant_label

from . import relay_support as rs, support


# --- item 1: acceptance -----------------------------------------------------------
def test_dur_admit__admission_takes_the_prepared_record_never_the_validation_record():
    """M2 request 5 / S2M D1: `prepare_request` replaces the record the ingress built, so the
    staged payload and the admitted job name our media ref - never the customer's URL."""
    world = rs.World()
    world.during.append(world.work)
    reply = rs.run(rs.call(world.app, rs.body(rs.VIDEO)))
    assert reply.status == 200, reply.body
    job = world.only_job()
    (ref,) = job.request.media
    staged = world.objects.objects[f"payloads/{world.org}/{job.id}.json"][1]
    assert rs.CLIP_URL.encode() not in staged and ref.handle.encode() in staged
    part = job.request.messages[0]["content"][1]
    assert part == {"type": "video_url", "video_url": {"ref": ref.handle}}, part
    assert world.media.by_job[job.id] == (ref,)          # attached to the admitted job


def test_api_modes__explicit_async_is_refused_before_anything_durable():
    """G3 owns `Prefer: respond-async`; until then this route refuses it with a typed error
    and no side effect, and it never answers 202."""
    world = rs.World()
    world.during.append(lambda: world.clock.advance(3_600))   # a wait would end, not hang
    reply = rs.run(rs.call(world.app, rs.body(), headers={"prefer": "respond-async"}))
    assert (reply.status, reply.json()["error"]["code"]) == (400, "unsupported_parameter")
    assert reply.json()["error"]["param"] == "Prefer"
    assert world.jobs.jobs == {} and world.objects.objects == {}
    assert world.jobs.wallet(world.org).reserved_total == 0


def test_api_modes__a_capability_drift_after_validation_cancels_the_admitted_job():
    """G1R Limit 2: the ingress checked `stream` against the revision it resolved; if the
    pinned revision no longer streams by admission time, the job just admitted is cancelled
    (nothing ran, nothing is billed) and the caller gets the typed refusal, not a stream."""
    world = rs.World(regime=CREDIT)
    admit = world.jobs.admit_credit
    serving = world.catalog.servings[support.IDS.serving_version]

    async def drifted(request, idem):
        capability = serving.capability.model_copy(update={"stream_output": False})
        world.catalog.servings[serving.serving_version_id] = serving.model_copy(
            update={"capability": capability})
        return await admit(request, idem)

    world.jobs.admit_credit = drifted
    reply = rs.run(rs.call(world.app, rs.body(stream=True)))
    assert reply.status == 400 and reply.headers["content-type"] == "application/json"
    assert reply.json()["error"]["code"] == "unsupported_parameter"
    job = world.only_job()
    assert (job.state, job.outcome.settlement_state) == (JobState.cancelled,
                                                          SettlementState.released_free)
    assert world.released(job)


def test_api_modes__a_card_this_deployment_did_not_approve_is_refused_and_released():
    """Wire-in request "G1R (active card)": a pinned card that is not
    `ACTIVE_RATE_CARD_VERSION` is unpriced here (R69) - refused, and the hold released."""
    world = rs.World(regime=CREDIT)
    world.relay.active_rate_card_version = "rc_not_approved_here"
    reply = rs.run(rs.call(world.app, rs.body()))
    assert (reply.status, reply.json()["error"]["code"]) == (400, "invalid_request")
    job = world.only_job()
    assert job.state is JobState.cancelled and world.released(job)


# --- item 2: the synchronous wait -------------------------------------------------
def test_api_modes__success_is_answered_only_after_the_terminal_commit():
    """Published output is not a success: nothing is sent while the job runs, and the 200
    carries the committed result once the settling transaction has committed."""
    world = rs.World()
    started = []
    box = {}

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    world.during += [publish, lambda: None, lambda: None,
                     lambda: world.complete(box["lease"])]

    def on_send(message):
        if message["type"] == "http.response.start":
            started.append(world.only_job().state)

    reply = rs.run(rs.call(world.app, rs.body(), on_send=on_send))
    assert started == [JobState.succeeded], started
    assert reply.status == 200, reply.body
    answer = reply.json()
    assert answer["choices"][0]["message"]["content"] == "Two people unload boxes."
    assert answer["usage"] == {"prompt_tokens": 1200, "completion_tokens": 5,
                               "total_tokens": 1205}
    assert answer["id"] == f"chatcmpl-{world.only_job().id}"
    assert reply.headers[wire.HEADER_INFERENCE_ID] == world.only_job().id


def test_api_modes__a_sync_disconnect_cancels_durably():
    """The client left mid-generation: the job is cancelled in the store (the worker's next
    fenced write is refused), nothing is sent, and a published job's hold waits for
    reconciliation with no debit (R21)."""
    world = rs.World()
    leave = asyncio.Event()
    box = {}

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    world.during += [publish, leave.set]

    async def body():
        reply = await rs.call(world.app, rs.body(), leave=leave)
        with pytest.raises(errors.AlreadyTerminal):
            await world.commit(box["lease"], " unload")
        return reply

    reply = rs.run(body())
    assert reply.messages == []
    job = world.only_job()
    assert (job.state, job.outcome.debit) == (JobState.cancelled, Decimal(0))
    assert world.jobs.holds[job.id].state is HoldState.unknown


def test_api_modes__a_sync_wait_cancelled_from_outside_still_cancels_the_job():
    """No identity reached a sync caller, so a wait that is itself cancelled (the process
    stopping) cancels the job - and that cancel is shielded: cancelling the handler again
    while the store call is in flight does not abort it."""
    world = rs.World()
    release = asyncio.Event()
    cancel = world.jobs.cancel
    cancels = []

    async def slow_cancel(org_id, handle):
        cancels.append(handle)
        await release.wait()
        return await cancel(org_id, handle)

    world.jobs.cancel = slow_cancel

    async def body():
        task = asyncio.ensure_future(rs.call(world.app, rs.body()))
        world.during.append(lambda: asyncio.current_task().cancel())
        while not cancels:
            await asyncio.sleep(0)
        task.cancel()                        # the handler is cancelled a second time
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        await asyncio.gather(*world.relay._cancels)

    rs.run(body())
    assert world.only_job().state is JobState.cancelled


def test_api_modes__a_sync_timeout_cancels_and_answers_the_deadline():
    """Past the stored deadline (plus the grace) the gateway cancels and answers 504. A
    cancel after publication settles `held_unknown` with debit 0 and is rendered as
    cancelled - never failed, never billed."""
    world = rs.World()

    async def publish():
        await world.commit(await world.lease(), "Two people")

    world.during += [publish, lambda: world.clock.advance(3_600)]
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.status == 504, reply.body
    error = reply.json()["error"]
    assert (error["code"], error["infrx"]["state"]) == ("deadline_exceeded", "cancelled")
    job = world.only_job()
    assert (job.state, job.outcome.settlement_state) == (JobState.cancelled,
                                                          SettlementState.held_unknown)
    assert job.outcome.debit == 0


def test_api_modes__a_timeout_that_races_a_committed_result_returns_the_result():
    """01: durable state wins. The job completed between the last poll and the deadline, so
    the gateway's cancel answers the committed completion and the caller gets it."""
    world = rs.World()
    box = {}

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    async def complete_then_expire():
        await world.complete(box["lease"])
        world.clock.advance(3_600)

    world.during += [publish, complete_then_expire]
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.status == 200, reply.body
    assert reply.json()["choices"][0]["message"]["content"] == "Two people unload boxes."
    assert world.only_job().outcome.settlement_state is SettlementState.settled


def test_api_modes__server_timing_and_the_registry_count_what_the_gateway_saw():
    """I3B request 3 / E1B request 5: `Server-Timing` names the phase the gateway measured
    (the worker's timings have no durable carrier yet, W3 request 9), and an injected
    registry counts acceptances and refusals by hashed tenant; none injected, none counted."""
    world = rs.World()
    world.relay.registry = registry = Registry("gateway")
    world.during.append(world.work)
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.status == 200
    assert reply.headers[wire.HEADER_SERVER_TIMING].startswith("prepare;dur=")
    assert registry.value("infrx_jobs_accepted_total", mode="sync", tenant=world.org) == 1
    assert registry.value("infrx_phase_seconds", phase="prepare") == 1
    refused = rs.run(rs.call(world.app, rs.body(), headers={"prefer": "respond-async"}))
    assert refused.status == 400
    assert registry.value("infrx_requests_rejected_total", code="unsupported_parameter",
                          tenant=world.org) == 1
    assert tenant_label(world.org) in registry.render()
    assert world.org not in registry.render()


# --- item 4: rendering the committed outcome -----------------------------------------
def test_api_modes__a_foreign_or_unknown_handle_is_not_found_and_changes_nothing():
    """D3 request 2: another tenant's handle is `not_found` exactly like an unknown one."""
    world = rs.World()

    async def body():
        task = asyncio.ensure_future(rs.call(world.app, rs.body()))
        while not world.jobs.jobs:
            await asyncio.sleep(0)
        handle = world.only_job().admission.job_handle
        for org_id, name in ((support.IDS.consumer_org, handle), (world.org, "job_unknown")):
            with pytest.raises(errors.NotFound):
                await world.relay.cancel(org_id, name)
        assert not world.only_job().terminal
        world.during.append(lambda: world.clock.advance(3_600))    # let the wait end
        assert (await task).status == 504

    rs.run(body())


def test_api_modes__a_replay_of_a_cancelled_job_is_rendered_cancelled_never_failed():
    """A job cancelled after publication (`held_unknown`, debit 0) and asked for again by
    its idempotency key answers its committed state - cancelled - not a failure."""
    world = rs.World()
    leave = asyncio.Event()

    async def publish():
        await world.commit(await world.lease(), "Two people")

    world.during += [publish, leave.set]
    rs.run(rs.call(world.app, rs.body(), key="k-1", leave=leave))
    assert world.only_job().outcome.settlement_state is SettlementState.held_unknown
    reply = rs.run(rs.call(world.app, rs.body(), key="k-1"))
    assert reply.headers[wire.HEADER_IDEMPOTENCY_REPLAYED] == "true"
    error = reply.json()["error"]
    assert (reply.status, error["code"], error["infrx"]["state"]) == (409, "state_conflict",
                                                                      "cancelled")


def test_api_modes__a_credit_job_that_expires_unclaimed_is_rendered_expired():
    """D3 handback: a CREDIT job whose queue wait ran out is `expired` /
    `queue_wait_expired` / `released_free` - answered as a deadline, never billed."""
    world = rs.World(regime=CREDIT)

    async def expire():
        world.clock.advance(world.limits.queue_wait_interactive_s + 1)
        await world.jobs.recover()

    world.during += [world.prepare, expire]
    reply = rs.run(rs.call(world.app, rs.body()))
    error = reply.json()["error"]
    assert (reply.status, error["code"], error["infrx"]["state"]) == (504, "deadline_exceeded",
                                                                      "expired")
    job = world.only_job()
    assert (job.outcome.cause, job.outcome.settlement_state) == (
        TerminalCause.queue_wait_expired, SettlementState.released_free)
    assert world.released(job) and job.settlement is None


def test_api_modes__already_terminal_on_cancel_reads_the_committed_outcome():
    """D3 handback delta: a store that answers `already_terminal` to a cancel is asked for
    the outcome, which is what the caller gets - here the completion that won."""
    world = rs.World()
    box = {}

    async def publish():
        box["lease"] = await world.lease()

    async def complete_then_expire():
        await world.complete(box["lease"])
        world.clock.advance(3_600)

    async def already_terminal(org_id, handle):
        raise errors.AlreadyTerminal("terminalize_no_usage on a terminal job (H-4)")

    world.jobs.cancel = already_terminal
    world.during += [publish, complete_then_expire]
    reply = rs.run(rs.call(world.app, rs.body()))
    assert reply.status == 200, reply.body
    assert reply.json()["choices"][0]["message"]["content"] == "Two people unload boxes."
