#!/usr/bin/env python3
"""G2 item 6: the sync/SSE matrix and the DUR-OUTPUT drills, end to end in process.

    uv run --frozen pytest -q tests/g/test_relay_matrix.py

The cutover's app (metered ingress + `Relay`), the contract JobStore/StreamStore, M's real
`MediaUploads` (fetch, probe, stage, attach, prepare) and W's real `AttemptRunner` over the
scripted vLLM (`worker/fakes.FakeUpstream`, W1's `VllmEngine`) - one process, one clock.
Every drill asserts store state: job state, hold settled or released, journal, attempts.

Matrix: text and video-by-URL, each sync and SSE. Video by upload is G4U's (pending G4U).
The engine is W1's own, with W-new merged (a15fa4b): `stream`/`max_tokens` in the frozen
record's `parameters` are consumed by the record, not refused.
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.contracts import wire
from infrx.contracts.records import (ChunkEventType, HoldState, JobState, OutboxKind,
                                     SettlementState, TerminalCause)

from . import relay_support as rs

INPUTS = {"text": rs.TEXT, "video_url": rs.VIDEO}


def terminal_chunk(world, job_id):
    (chunk,) = [c for c in world.journal(job_id) if c.event_type is ChunkEventType.terminal]
    return chunk


@pytest.mark.parametrize("mode", ["sync", "stream"])
@pytest.mark.parametrize("kind", sorted(INPUTS))
def test_api_modes__the_sync_and_sse_matrix_answers_from_committed_state(kind, mode):
    """Each cell: accepted durably, executed by the real worker, answered from what the
    store committed - the result object (sync) or the journal (SSE) - with the settled usage,
    one settlement, capacity released and the journal ended by its terminal event."""
    world = rs.World()
    world.during.append(world.work)
    reply = rs.run(rs.call(world.app, rs.body(INPUTS[kind], stream=mode == "stream")))
    assert reply.status == 200, reply.body
    job = world.only_job()
    outcome = job.outcome
    assert (outcome.state, outcome.settlement_state) == (JobState.succeeded,
                                                         SettlementState.settled)
    assert outcome.debit > 0 and world.jobs.holds[job.id].state is HoldState.settled
    assert not any(r.active for r in job.reservations.values())
    usage = {"prompt_tokens": outcome.usage.prompt_tokens,
             "completion_tokens": outcome.usage.completion_tokens,
             "total_tokens": outcome.usage.total_tokens}
    if mode == "sync":
        answer = reply.json()
        assert answer["choices"][0]["message"]["content"] == world.results[job.id]
        assert answer["usage"] == usage and answer["object"] == "chat.completion"
    else:
        assert reply.text() == world.results[job.id]
        assert reply.data()[-2].get("usage") == usage
        token = terminal_chunk(world, job.id).cursor.token
        assert reply.frames[-1] == f"id: {token}\ndata: [DONE]"
    if kind == "video_url":
        (ref,) = world.media.prepared_by_job[job.id]
        assert ref.org_id == world.org and ref.duration_s == 4.0
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1


@pytest.mark.parametrize("mode", ["sync", "stream"])
def test_dur_output__a_kill_before_the_first_committed_chunk_is_retried_to_one_output(mode):
    """A worker lost before it committed anything: `recover` requeues the attempt (nothing
    was published) and the next worker's is the one output - relayed once, settled once."""
    world = rs.World()

    async def lost_then_retried():
        job_id = await world.prepare()
        await world.jobs.claim(job_id, "worker-lost")            # dies before any append
        world.clock.advance(world.limits.lease_ttl_s + 1)
        await world.jobs.recover()
        await world.runner(world.upstream(), "worker-b").run(job_id)

    world.during.append(lost_then_retried)
    reply = rs.run(rs.call(world.app, rs.body(stream=mode == "stream")))
    job = world.only_job()
    assert job.outcome.state is JobState.succeeded and job.generation == 2
    assert {chunk.generation for chunk in world.journal(job.id)} == {2}
    text = reply.json()["choices"][0]["message"]["content"] if mode == "sync" else reply.text()
    assert text == world.results[job.id] == "Two people unload boxes from a van onto a trolley."
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1


@pytest.mark.parametrize("mode", ["sync", "stream"])
def test_dur_output__a_kill_after_the_first_committed_chunk_is_never_regenerated(mode):
    """After the publication marker a lost worker is never replaced: the job fails
    `lost_after_publication`, SSE relays the committed prefix then an honest error and
    `[DONE]`, sync answers the failure, and the hold waits for reconciliation - no debit."""
    world = rs.World()

    async def lost_after_publishing():
        await world.commit(await world.lease("worker-lost"), "Two people")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        await world.jobs.recover()

    world.during += [lost_after_publishing] + [lambda: None] * 3 \
        + [lambda: world.clock.advance(3_600)]
    reply = rs.run(rs.call(world.app, rs.body(stream=mode == "stream")))
    job = world.only_job()
    assert (job.outcome.cause, job.outcome.debit) == (TerminalCause.lost_after_publication, 0)
    assert world.jobs.holds[job.id].state is HoldState.unknown
    assert {chunk.generation for chunk in world.journal(job.id)} == {1}
    if mode == "sync":
        assert (reply.status, reply.json()["error"]["code"]) == (500, "internal_error")
    else:
        assert reply.status == 200 and reply.text() == "Two people"
        error = [item["error"] for item in reply.data()
                 if isinstance(item, dict) and "error" in item]
        assert [(e["code"], rs.state_of(e)) for e in error] == [("stream_interrupted",
                                                                      "failed")]
        assert reply.data()[-1] == "[DONE]"


def test_dur_output__a_lost_answer_is_replayed_by_key_with_one_settlement():
    """The terminal acknowledgment is lost twice over - the worker's (its `complete`
    committed and the answer died) and the client's (it never saw the 200). Retrying with the
    same Idempotency-Key answers the committed result for the same job, marked as a replay,
    and nothing is admitted, executed or settled again."""
    world = rs.World()
    world.failures.crash_after_commit("complete")
    world.during.append(world.work)
    first = rs.run(rs.call(world.app, rs.body(), key="order-7"))
    assert first.status == 200, first.body
    job = world.only_job()
    ledger = world.jobs.wallet(world.org).ledger_total
    again = rs.run(rs.call(world.app, rs.body(), key="order-7"))
    assert again.status == 200 and again.json() == first.json()
    assert again.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert wire.HEADER_IDEMPOTENCY_REPLAYED not in first.headers
    assert again.headers[wire.HEADER_INFERENCE_ID] == first.headers[wire.HEADER_INFERENCE_ID]
    assert world.only_job() is job and world.jobs.wallet(world.org).ledger_total == ledger
    assert world.jobs.outbox_kinds(job.id).count(OutboxKind.usage_projection) == 1


@pytest.mark.parametrize("mode", ["sync", "stream"])
@pytest.mark.parametrize("fault", ["engine_error_pre_headers", "engine_error_post_headers"])
def test_api_stream__an_upstream_error_is_an_honest_terminal_error(fault, mode):
    """An engine that fails before or after its own headers: the job fails and is not
    billed; sync answers the failure's envelope, SSE (already 200) relays what was
    committed, then an `infrx.error` frame and `[DONE]`. A refusal before the gateway's own
    headers - here, no credit - is the JSON envelope with its status, even on a stream."""
    world = rs.World()
    world.during.append(lambda: world.work(fault))
    reply = rs.run(rs.call(world.app, rs.body(stream=mode == "stream")))
    job = world.only_job()
    assert (job.state, job.outcome.debit) == (JobState.failed, 0)
    # Review stream-S8: nothing in flight is left behind, and the hold is off the wallet.
    assert not any(r.active for r in job.reservations.values())
    assert world.jobs.wallet(world.org).reserved_total == 0
    if mode == "sync":
        assert (reply.status, reply.json()["error"]["code"]) == (500, "internal_error")
    else:
        assert reply.status == 200
        error = [item["error"] for item in reply.data()
                 if isinstance(item, dict) and "error" in item]
        assert [e["code"] for e in error] == ["stream_interrupted"]
        assert reply.data()[-1] == "[DONE]"
        committed = "".join(c.payload.get("visible", "") for c in world.journal(job.id))
        assert reply.text() == committed

    broke = rs.World(grant="0.000001")
    refused = rs.run(rs.call(broke.app, rs.body(stream=mode == "stream")))
    assert (refused.status, refused.json()["error"]["code"]) == (402, "insufficient_credit")
    assert refused.headers["content-type"] == "application/json" and broke.jobs.jobs == {}


@pytest.mark.parametrize("outage", ["database", "object_store"])
def test_dur_admit__an_outage_at_acceptance_is_a_retryable_503_with_nothing_admitted(outage):
    """A durable dependency that fails at acceptance: a typed 503 with `Retry-After`, the
    driver's text in no answer, and no job, hold or journal reservation behind. (The index
    is not touched at acceptance at all: dispatch is the outbox's, Q3's relay feeds it.)
    Objects already staged are not claimed gone (review money-N3): a payload or source
    object whose admission never committed is the collector's stray sweep (M, carried)."""
    world = rs.World()
    if outage == "database":
        world.failures.fail("admit", error=ConnectionError(
            "connection to postgresql://infrx:secret@db:5432 refused"))
    else:
        world.objects.down = True
    reply = rs.run(rs.call(world.app, rs.body()))
    assert (reply.status, reply.json()["error"]["code"]) == (503, "dependency_unavailable")
    assert reply.headers[wire.HEADER_RETRY_AFTER]
    assert b"secret" not in reply.body and b"s3.internal" not in reply.body
    assert world.jobs.jobs == {} and world.jobs.journal.total() == 0
    assert world.jobs.wallet(world.org).reserved_total == 0


def test_api_stream__a_gateway_restart_mid_stream_leaves_the_job_to_its_worker():
    """The gateway process stops while it relays (its task is cancelled): the client already
    holds the job's identity, so the job is not cancelled - its worker finishes it. The next
    process answers the same key's retry with the whole committed journal, once."""
    world = rs.World()
    box = {}

    async def publish_then_stop():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    world.during += [publish_then_stop, lambda: asyncio.current_task().cancel()]

    async def first_process():
        with pytest.raises(asyncio.CancelledError):
            await rs.call(world.app, rs.body(stream=True), key="resume-1")

    rs.run(first_process())
    job = world.only_job()
    assert job.state is JobState.running                          # left to its worker

    async def worker_finishes():
        await world.commit(box["lease"], " unload boxes.")
        await world.complete(box["lease"])

    rs.run(worker_finishes())
    world.restart()
    reply = rs.run(rs.call(world.app, rs.body(stream=True), key="resume-1"))
    assert reply.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) == "true"
    assert reply.data()[0].get("job_handle") == job.admission.job_handle
    assert reply.text() == "Two people unload boxes."
    token = terminal_chunk(world, job.id).cursor.token
    assert reply.frames[-1] == f"id: {token}\ndata: [DONE]"
    assert world.only_job() is job and job.outcome.state is JobState.succeeded
