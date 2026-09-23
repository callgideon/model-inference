#!/usr/bin/env python3
"""API-STREAM / DUR-OUTPUT: G2's persistent SSE relay over the committed journal.

    uv run --frozen pytest -q tests/g/test_relay_sse.py

The relay's only source of bytes is `StreamStore.read_owned`: every model frame on the wire
is a chunk the journal committed, `visible` text only. Cases drive the journal by hand
(`World.lease` / `World.commit`) where they must stop between two commits, and W's real
attempt runner where a whole answer is the point.
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.contracts.fakes.engine import SPLIT_REASONING_VISIBLE
from infrx.contracts.records import (ChunkEventType, EngineEvent, HoldState, JobState,
                                     SettlementState, TerminalCause)

from . import relay_support as rs


def stream(world, **kw) -> rs.Reply:
    return rs.run(rs.call(world.app, rs.body(stream=True), **kw))


def test_api_stream__the_first_frame_names_the_job_before_any_model_output():
    """The identity a client resumes with after a lost connection (G3's events route): the
    first frame is `infrx.progress` carrying the job handle and the request id, sent once
    acceptance is durable and before the job has produced anything. It carries no `id`: an
    id is a journal cursor, and this frame is not a journal event."""
    world = rs.World()
    seen = {}

    def on_send(message):
        if message["type"] == "http.response.start":
            seen["jobs_at_headers"] = len(world.jobs.jobs)
        if message["type"] == "http.response.body" and "first" not in seen:
            seen["first"] = (message["body"].decode(), world.only_job().state)

    world.during.append(world.work)
    reply = stream(world, on_send=on_send)
    frame, state = seen["first"]
    assert seen["jobs_at_headers"] == 1 and state is JobState.preparing
    assert frame.startswith("event: infrx.progress\ndata: ") and "\nid:" not in frame
    job = world.only_job()
    assert reply.data()[0] == {"job_handle": job.admission.job_handle, "request_id": job.id,
                               "phase": "accepted"}
    assert reply.headers["content-type"].startswith("text/event-stream")


def test_api_stream__keepalives_are_comments_never_data():
    """While nothing new is committed, a comment every SSE_KEEPALIVE_S keeps the connection
    open - never a data frame, never something a client would parse as model output."""
    world = rs.World()
    box = {}

    async def begin():
        box["lease"] = await world.lease()

    def quiet():
        world.clock.advance(world.limits.sse_keepalive_s / 2 + 0.5)

    async def finish():
        await world.commit(box["lease"], "Two people unload boxes.")
        await world.jobs.cancel(world.org, world.only_job().admission.job_handle)

    world.during += [begin, quiet, quiet, quiet, finish]
    reply = stream(world)
    comments = [frame for frame in reply.frames if frame.startswith(":")]
    assert comments and all(frame == ": keepalive" for frame in comments), reply.frames
    assert all(not frame.startswith(":") or "data:" not in frame for frame in reply.frames)
    assert reply.text() == "Two people unload boxes."


def test_api_stream__only_visible_text_reaches_the_wire():
    """R58/R80: the journal may carry `raw` (the reasoning block, split across chunks); the
    wire carries `visible` only. Shown on a hand-written journal whose `raw` splits
    `<think>` across three chunks, and on W's real runner and reasoning filter."""
    world = rs.World()

    async def split():
        lease = await world.lease()
        await world.stream.append(lease, tuple(
            EngineEvent(type=ChunkEventType.delta, payload={"raw": raw, "visible": visible})
            for raw, visible in (("<th", ""), ("ink>the van is</thi", ""),
                                 ("nk>Two people.", "Two people."))))
        await world.jobs.cancel(world.org, world.only_job().admission.job_handle)

    world.during.append(split)
    reply = stream(world)
    assert reply.text() == "Two people."
    assert "think" not in reply.body.decode() and "van is" not in reply.body.decode()

    world = rs.World()
    world.during.append(lambda: world.work("split_reasoning_delimiters"))
    reply = stream(world)
    assert reply.text() == "".join(SPLIT_REASONING_VISIBLE)
    assert "think" not in reply.body.decode() and "stationary" not in reply.body.decode()


def test_api_stream__done_is_sent_only_after_the_terminal_commit():
    """Committed deltas are relayed as they land, but `[DONE]` waits for the settling
    transaction: while the job is still running, the stream stays open."""
    world = rs.World()
    box = {}
    at_done = []

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    async def settle():
        await world.complete(box["lease"])

    def on_send(message):
        if b"[DONE]" in message.get("body", b""):
            at_done.append(world.only_job().state)

    world.during += [publish, lambda: None, lambda: None, settle]
    reply = stream(world, on_send=on_send)
    assert at_done == [JobState.succeeded], at_done
    assert reply.text() == "Two people"
    terminal = [c for c in world.journal(world.only_job().id)
                if c.event_type is ChunkEventType.terminal]
    assert reply.frames[-1] == f"id: {terminal[0].cursor.token}\ndata: [DONE]"


def test_api_stream__a_disconnect_mid_stream_cancels_durably():
    """The client left after the first committed chunk: the job is cancelled in the store
    (the worker's next write is refused) and nothing more is relayed."""
    world = rs.World()
    leave = asyncio.Event()
    box = {}

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    world.during += [publish, leave.set]
    reply = stream(world, leave=leave)
    job = world.only_job()
    assert job.state is JobState.cancelled
    assert job.outcome.cause is TerminalCause.client_disconnected    # R21: the true cause
    assert world.jobs.holds[job.id].state is HoldState.unknown       # published: reconcile
    assert reply.text() == "Two people" and "[DONE]" not in reply.body.decode()


def test_api_stream__a_client_gone_before_the_first_byte_still_cancels():
    """The generator-never-started path: the client is gone before anything is sent and
    the first send fails. The response owns the job from acceptance on, so the job is
    cancelled and its hold released - there is no generator whose `finally` never ran."""
    world = rs.World()
    leave = asyncio.Event()
    leave.set()

    def on_send(message):
        if message["type"] == "http.response.start":
            raise OSError("the peer reset the connection")      # ASGI 2.4: send fails

    reply = stream(world, leave=leave, on_send=on_send)
    job = world.only_job()
    assert job.state is JobState.cancelled
    assert job.outcome.cause is TerminalCause.client_disconnected
    assert job.outcome.settlement_state is SettlementState.released_free
    assert world.jobs.wallet(world.org).reserved_total == 0
    assert len(reply.messages) == 1                              # the failed start only


def test_api_stream__a_replay_gap_ends_the_stream_honestly_and_cancels():
    """A cursor behind the prune watermark is a gap the relay cannot fill: an `infrx.error`
    naming it, then `[DONE]` once the job it ended is terminal - never a silently shorter
    answer, and never a job left running for nobody."""
    world = rs.World(limits=rs.DEFAULTS.replace(journal_chunk_ttl_s=5.0))
    box = {}

    async def begin():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two ")

    async def prune_behind_the_cursor():
        await world.commit(box["lease"], "people ")
        world.clock.advance(6)
        await world.commit(box["lease"], "unload")
        assert await world.stream.expire() == 2

    world.during += [begin, prune_behind_the_cursor]
    reply = stream(world)
    assert reply.text() == "Two "
    error = [item for item in reply.data() if isinstance(item, dict) and "error" in item]
    assert [e["error"]["code"] for e in error] == ["replay_gap"]
    assert reply.data()[-1] == "[DONE]"
    assert world.only_job().state is JobState.cancelled


def test_api_stream__without_a_terminal_event_the_committed_outcome_ends_the_stream():
    """D3's terminalizations write no terminal journal event until D4's trigger: the relay
    ends on the outcome `get_owned` answers once the journal holds nothing more. A job
    cancelled elsewhere (G3's DELETE) is rendered cancelled, then `[DONE]`."""
    world = rs.World()
    world.jobs.stream = None                  # terminalization writes no journal event

    async def cancelled_elsewhere():
        await world.commit(await world.lease(), "Two people")
        await world.jobs.cancel(world.org, world.only_job().admission.job_handle)

    world.during += [cancelled_elsewhere] + [lambda: None] * 3 \
        + [lambda: world.clock.advance(3_600)]
    reply = stream(world)
    assert reply.text() == "Two people"
    error = [item["error"] for item in reply.data() if isinstance(item, dict) and "error" in item]
    assert [(e["code"], rs.state_of(e)) for e in error] == [("state_conflict", "cancelled")]
    assert reply.frames[-1] == "data: [DONE]"


def test_api_stream__a_journal_read_that_fails_is_retried_not_the_end():
    """A journal read that fails for want of the database - here while the job is still
    running - is retried at the next poll: it is neither an empty journal (which could end
    the stream) nor the end of the job, so the job is not cancelled."""
    world = rs.World()
    box = {}

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two people")

    async def settle():
        assert world.only_job().state is JobState.running      # the read failed meanwhile
        await world.complete(box["lease"])

    world.failures.fail("read_owned", on_call=2,
                        error=ConnectionError("postgresql://infrx:secret@db/infrx reset"))
    world.during += [publish, settle]
    reply = stream(world)
    job = world.only_job()
    assert world.failures.count("read_owned") >= 3
    assert (job.state, job.outcome.cause) == (JobState.succeeded, TerminalCause.completed)
    assert reply.text() == "Two people" and reply.data()[-1] == "[DONE]"
    assert "secret" not in reply.body.decode()


# === G2 review round 2 =====================================================================
def test_api_stream__a_read_that_fails_after_the_outcome_is_known_still_drains():
    """Review stream-S5. Without a terminal event (D3 today) the committed outcome ends the
    stream once the journal holds nothing more. A read that fails after that outcome is
    known is not "nothing more": what was committed before the settlement still reaches
    the client, then `[DONE]`."""
    world = rs.World()
    world.jobs.stream = None                  # terminalization writes no journal event
    read = world.stream.read_owned
    box = {}

    async def publish():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two ")

    async def settled_behind_the_read(org_id, handle, cursor, limit):
        chunks, cursor = await read(org_id, handle, cursor, limit)
        if not chunks and "lease" in box and "settled" not in box:
            box["settled"] = True             # committed and settled after this read ...
            await world.commit(box["lease"], "people")
            await world.complete(box["lease"], "Two people")
            world.failures.fail("read_owned", on_call=world.failures.count("read_owned") + 1,
                                error=ConnectionError("journal read reset"))  # ... next fails
        return chunks, cursor

    world.stream.read_owned = settled_behind_the_read
    world.during.append(publish)
    reply = stream(world)
    assert "settled" in box and world.only_job().state is JobState.succeeded
    assert reply.text() == "Two people" and reply.data()[-1] == "[DONE]"


@pytest.mark.parametrize("blocked", ["headers", "identity_frame"])
def test_api_stream__a_stream_cancelled_before_its_identity_frame_cancels_the_job(blocked):
    """Review stream-S2 / honesty-H-B3, r2 stream-C2-2. The process stops while the headers,
    or the identity frame itself, are still being sent: no identity reached the client, so
    nobody can resume the job - it is cancelled (an orphan otherwise), its hold released
    and nothing left reserved."""
    world = rs.World()

    def holds_the_send(message) -> bool:
        if blocked == "headers":
            return message["type"] == "http.response.start"
        return b"job_handle" in message.get("body", b"")

    async def body():
        started = asyncio.Event()

        def on_send(message):
            if holds_the_send(message):
                started.set()
                return asyncio.Event().wait()   # a peer that never reads

        task = asyncio.ensure_future(rs.call(world.app, rs.body(stream=True), on_send=on_send))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await world.relay.drain(1.0)

    rs.run(body())
    job = world.only_job()
    assert job.state is JobState.cancelled, job.state
    assert job.outcome.cause is TerminalCause.client_cancelled
    assert world.jobs.wallet(world.org).reserved_total == 0
    assert not any(r.active for r in job.reservations.values())


def test_api_stream__a_process_stop_after_the_client_left_cancels_as_disconnected():
    """Review stream-S7. The client left, and the process stopped before the relay's next
    look: the identity it holds is no reason to leave the job running for a client that is
    gone. The job is cancelled, recorded `client_disconnected`."""
    world = rs.World()
    leave = asyncio.Event()

    async def publish():
        await world.commit(await world.lease(), "Two people")

    async def leave_then_stop():
        leave.set()
        for _ in range(3):
            await asyncio.sleep(0)            # the watcher sees the disconnect
        asyncio.current_task().cancel()

    world.during += [publish, leave_then_stop]

    async def body():
        try:
            await rs.call(world.app, rs.body(stream=True), leave=leave)
        except asyncio.CancelledError:
            pass
        await world.relay.drain(1.0)

    rs.run(body())
    job = world.only_job()
    assert job.state is JobState.cancelled, job.state
    assert job.outcome.cause is TerminalCause.client_disconnected


def test_api_stream__nothing_but_visible_text_reaches_the_wire():
    """Review stream-S3 (R58/R80). A delta with `raw` and its `content` alias but no
    `visible` is relayed as nothing; a usage event and an engine error event carry nothing
    of the customer's (the counts come with the settled outcome, the failure as the
    terminal outcome). None of their bytes reach the wire."""
    world = rs.World()

    async def mixed():
        lease = await world.lease()
        await world.stream.append(lease, (
            EngineEvent(type=ChunkEventType.delta,
                        payload={"raw": "<think>RAW-REASONING", "content": "CONTENT-ALIAS"}),
            EngineEvent(type=ChunkEventType.usage,
                        payload={"prompt_tokens": 91919, "completion_tokens": 7}),
            EngineEvent(type=ChunkEventType.error, payload={"message": "ENGINE-TEXT"}),
            EngineEvent(type=ChunkEventType.delta,
                        payload={"raw": "Two people.", "visible": "Two people."})))
        await world.jobs.cancel(world.org, world.only_job().admission.job_handle)

    world.during.append(mixed)
    reply = stream(world)
    assert reply.text() == "Two people."
    wire_text = reply.body.decode()
    for secret in ("RAW-REASONING", "CONTENT-ALIAS", "91919", "ENGINE-TEXT"):
        assert secret not in wire_text, secret


def test_api_stream__a_stream_past_its_bound_cancels_with_sync_deadline():
    """Review stream-S4 / honesty-H-B2. The job is still running when the clock passes its
    stored deadline plus the grace: the relay cancels it (`sync_deadline`, R21), and the
    stream ends with `deadline_exceeded` naming the committed state, then `[DONE]`."""
    world = rs.World()

    async def publish():
        await world.commit(await world.lease(), "Two people")

    world.during += [publish, lambda: world.clock.advance(3_600)]

    async def body():
        try:
            return await asyncio.wait_for(rs.call(world.app, rs.body(stream=True)), 5)
        except asyncio.TimeoutError:
            pytest.fail("the stream never ended: its bound was not checked")

    reply = rs.run(body())
    job = world.only_job()
    assert (job.state, job.outcome.cause) == (JobState.cancelled, TerminalCause.sync_deadline)
    error = [item["error"] for item in reply.data() if isinstance(item, dict) and "error" in item]
    assert [(e["code"], rs.state_of(e)) for e in error] == [("deadline_exceeded", "cancelled")]
    assert reply.text() == "Two people" and reply.frames[-1] == "data: [DONE]"


def test_api_stream__a_gap_whose_cancel_is_unconfirmed_ends_without_done():
    """Review honesty-H-B1. A replay gap ends the relay, but the cancel cannot be confirmed
    (the store is unreachable): the last frame is the error, naming no state, and no
    `[DONE]` follows - that is sent only once a terminal commit is known. The driver's
    text is not echoed."""
    world = rs.World(limits=rs.DEFAULTS.replace(journal_chunk_ttl_s=5.0))
    box = {}

    async def unreachable(org_id, handle, **cause):
        raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")

    async def begin():
        box["lease"] = await world.lease()
        await world.commit(box["lease"], "Two ")

    async def prune_behind_the_cursor():
        await world.commit(box["lease"], "people ")
        world.clock.advance(6)
        await world.commit(box["lease"], "unload")
        assert await world.stream.expire() == 2
        world.jobs.cancel = unreachable

    world.during += [begin, prune_behind_the_cursor]
    reply = stream(world)
    assert "[DONE]" not in reply.body.decode() and "secret" not in reply.body.decode()
    last = reply.data()[-1]
    assert last["error"]["code"] == "replay_gap" and rs.state_of(last["error"]) is None
    assert world.only_job().state is JobState.running        # the cancel never committed
