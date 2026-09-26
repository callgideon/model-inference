#!/usr/bin/env python3
"""W3 / OPS-RECOVER + PERF-PILOT: the worker process - reaper, drain, readiness, timings.

    uv run --frozen pytest -q tests/w/test_service.py

On W2's world (the shared fake JobStore, StreamStore and Scheduler on one injected clock)
plus, for the process-loss drills, the integration engine: `tests/integration/fake_vllm.py`
as a real OS process on an ephemeral loopback port, spoken to by W1's real `VllmEngine`
over real HTTP. One case runs the service in a child process and sends it SIGTERM. No
GPU, no container, no database.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import pathlib
import signal
import socket
import subprocess
import sys

import httpx
import pytest

from infrx.contracts import errors
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import JobState, OutboxKind, TerminalCause, Usage
from infrx.worker import VllmEngine, WorkerLoop, WorkerService, prepared_request, server_timing
from infrx.worker import service as service_module
from infrx.worker.attempt import TIMED_PHASES
from infrx.worker.fakes import SERVED_MODEL, FakeUpstream, m2_local_uri
from tests.w.test_engine import Box, video_work
from tests.w.test_loop import (PROGRESS, ScriptEngine, World, candidate, delta, queued, run,
                               usage_event)

API = pathlib.Path(__file__).resolve().parents[2]
REPO = API.parents[1]


def module_at(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def eventually(predicate, within_s: float = 5.0) -> bool:
    """Poll until `predicate()` or the bound; the answer is asserted by the caller, so a
    condition that never arrives fails as an assertion rather than as a timeout."""
    deadline = asyncio.get_running_loop().time() + within_s
    while not predicate() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    return bool(predicate())


async def http_get(port: int, path: str, method: str = "GET") -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nhost: probe\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


class Blocking(ScriptEngine):
    """Starts, then never finishes: an attempt a drain must release."""

    async def _events(self):
        self.started += 1
        try:
            yield PROGRESS
            await asyncio.Event().wait()
            yield delta("never")
        finally:
            self.closed += 1


class Answering(ScriptEngine):
    """Answers after `pause_s` of real time: an attempt a drain can wait for."""

    pause_s: float = 0.05

    async def _events(self):
        self.started += 1
        yield PROGRESS
        await asyncio.sleep(self.pause_s)
        yield delta("an answer ")
        yield usage_event(Usage.of(1200, 1))


def service_for(world: World, engine, **kw) -> WorkerService:
    loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(engine),
                      worker_id="worker-w3", limits=world.limits)
    return WorkerService(loop=loop, jobs=world.jobs, engine=engine, **kw)


# --------------------------------------------------------------------------
# (2) the media root is one setting
# --------------------------------------------------------------------------
def test_api_stream__the_media_root_is_the_processing_cache_setting():
    """W2 request 5 / R61 (2): the adapter's root is `PROCESSING_CACHE_DIR`, the same
    setting M2's processing cache and serve.sh's `--allowed-local-media-path` read. Unset,
    every video is refused `dependency_unavailable` - never a guessed path."""
    box = Box()
    prepared = prepared_request(video_work(box), 1200)
    configured = DEFAULTS.replace(processing_cache_dir="/srv/cache")
    engine = VllmEngine(FakeUpstream(clock=box.clock).client(), served_model=SERVED_MODEL,
                        clock=box.clock, limits=configured, local_uri=m2_local_uri("/srv/cache"))
    url = engine.upstream_body(prepared)["messages"][0]["content"][1]["video_url"]["url"]
    assert url == m2_local_uri("/srv/cache")(prepared.media[0])
    unset = VllmEngine(FakeUpstream(clock=box.clock).client(), served_model=SERVED_MODEL,
                       clock=box.clock, limits=DEFAULTS, local_uri=m2_local_uri("/srv/cache"))
    with pytest.raises(errors.DependencyUnavailable):
        unset.upstream_body(prepared)


# --------------------------------------------------------------------------
# the reaper (E3B dr03/dr04/dr06/dr13 call `recover`; this is what calls it in service)
# --------------------------------------------------------------------------
def test_ops_recover__the_reaper_requeues_lost_leases_and_dispatches_them():
    """`recover`, and every index event it returns enqueued, so a dead worker's job runs
    again as a new generation instead of waiting for its absolute deadline. (A lost
    *preparation* lease is re-dispatched through the store's outbox, not in `recover`'s
    return value - the outbox relay is Q's; see the W3 evidence.)"""
    async def case():
        world = World()
        request, _ = await queued(world)
        other, _ = await queued(world)
        published, _ = await queued(world)
        dead = await world.jobs.claim(request.request_id, "worker-dead")
        await world.jobs.claim(other.request_id, "worker-dead")
        lost = await world.jobs.claim(published.request_id, "worker-dead")
        await world.stream.append(lost, (delta("published "),))
        service = service_for(world, Answering(), reap_interval_s=3600)
        assert await service.reap_once() == 0                  # nothing has expired yet
        world.clock.advance(world.limits.lease_ttl_s + 1)
        # three leases died; the published one comes back as an outcome, not a candidate
        assert await service.reap_once() == 2
        assert world.outcome(published.request_id).cause is TerminalCause.lost_after_publication
        assert sorted((e.job_id, e.kind, e.attempt) for e in world.scheduler.pending.values()
                      ) == sorted((job, OutboxKind.inference_dispatch, 1)
                                  for job in (request.request_id, other.request_id))
        assert service.reaped == 2 and service.reap_errors == 0

        # a requeued attempt is an ordinary candidate for the pool, one generation later
        results = await service.loop.run(concurrency=1)
        assert sorted((r.job_id, r.generation, r.cause) for r in results) == sorted(
            (job, dead.generation + 1, TerminalCause.completed)
            for job in (request.request_id, other.request_id))
    run(case())


def test_ops_recover__a_restart_reaps_before_it_claims_and_survives_a_failing_store():
    """`start` reaps first, so a restarted worker requeues what died with its predecessor
    before its pool looks at the index; a `recover` or `enqueue` that fails is counted and
    retried on the next tick, never fatal to the process."""
    async def case():
        world = World()
        request, _ = await queued(world)
        await world.jobs.claim(request.request_id, "worker-dead")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        other, _ = await queued(world)                # a candidate already in the index
        await world.scheduler.enqueue(candidate(world, other))
        service = service_for(world, Answering(), reap_interval_s=3600)
        recover, claimed_at_reap = world.jobs.recover, []

        async def a_round_trip():
            await asyncio.sleep(0.05)                 # long enough for a running pool to claim
            claimed_at_reap.append(service.loop.claimed)
            return await recover()
        world.jobs.recover = a_round_trip
        await service.start()
        assert service.reaped == 1                    # before the first tick of any timer
        assert claimed_at_reap == [0], "the pool claimed before the start-up reap finished"
        assert await eventually(lambda: world.outcome(request.request_id) is not None
                                and world.outcome(other.request_id) is not None)
        await service.stop()
        assert world.outcome(request.request_id).cause is TerminalCause.completed

        broken = World()
        service = service_for(broken, Answering(), reap_interval_s=3600, health_port=0)

        async def refuses():
            raise ConnectionError("the database is gone")
        broken.jobs.recover = refuses
        assert await service.reap_once() == 0 and service.reap_errors == 1
        await service.start()                         # its own start-up reap fails too
        status, body = await http_get(service._server.sockets[0].getsockname()[1], "/readyz")
        assert (status, body["reap_errors"]) == (200, 2), body
        await service.stop()

        again = World()
        request, _ = await queued(again)
        await again.jobs.claim(request.request_id, "worker-dead")
        again.clock.advance(again.limits.lease_ttl_s + 1)
        service = service_for(again, Answering(), reap_interval_s=3600)

        async def full(event):
            raise ConnectionError("the index is gone")
        again.scheduler.enqueue = full
        assert await service.reap_once() == 1 and service.reap_errors == 1
        # the requeue itself is durable: the store holds it queued for the reconciler
        assert again.jobs.jobs[request.request_id].state is JobState.queued
    run(case())


def test_ops_recover__the_reaper_runs_on_its_timer():
    """Not only at start-up: a lease that dies while the service runs is reaped by the
    timer and completed by the same pool."""
    async def case():
        world = World()
        service = service_for(world, Answering(), reap_interval_s=0.02)
        await service.start()
        request, _ = await queued(world)
        await world.jobs.claim(request.request_id, "worker-dead")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        assert await eventually(lambda: world.outcome(request.request_id) is not None)
        assert service.reaped == 1
        # the runner is free again, and readiness says so
        assert await eventually(lambda: not service.loop.in_flight)
        assert (await service.readiness())["loop"] == "idle"
        await service.stop()
        assert world.outcome(request.request_id).cause is TerminalCause.completed
    run(case())


# --------------------------------------------------------------------------
# (3) drain
# --------------------------------------------------------------------------
def test_ops_recover__a_drain_past_its_bound_releases_and_the_job_is_requeued_not_lost():
    """Stop claiming, wait within the bound, cancel the rest - released, never settled -
    and record it. The released job keeps its lease; past the TTL the next worker's
    reaper requeues it and it completes exactly once, one generation later."""
    async def case():
        world = World()
        request, _ = await queued(world)
        waiting, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        engine = Blocking()
        service = service_for(world, engine, drain_s=0.05, reap_interval_s=3600)
        await service.start()
        assert await eventually(lambda: engine.started)
        await world.scheduler.enqueue(candidate(world, waiting))
        report = await service.stop()
        assert report.released_jobs == (request.request_id,) and report.ended == ()
        assert engine.closed == 1                         # the stream was closed on the way out
        assert world.outcome(request.request_id) is None  # nothing settled
        assert world.jobs.jobs[request.request_id].state is JobState.running
        assert world.outcome(waiting.request_id) is None  # draining claims nothing new

        world.clock.advance(world.limits.lease_ttl_s + 1)
        successor = service_for(world, Answering(), reap_interval_s=3600)
        await successor.start()
        assert await eventually(lambda: world.outcome(request.request_id) is not None
                         and world.outcome(waiting.request_id) is not None)
        await successor.stop()
        outcome = world.outcome(request.request_id)
        assert outcome.state is JobState.succeeded and outcome.debit > 0
        generations = {r.job_id: r.generation for r in successor.loop.results}
        assert generations[request.request_id] == 2
    run(case())


def test_ops_recover__a_drain_records_what_finished_inside_its_bound():
    """The other half: an attempt that can finish inside the bound is waited for, settles
    normally, and the report says so (job and cause), with nothing released."""
    async def case():
        world = World()
        request, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        service = service_for(world, Answering(), drain_s=5.0, reap_interval_s=3600)
        await service.start()
        assert await eventually(lambda: service.loop.in_flight)
        report = await service.stop()
        assert report.ended == ((request.request_id, "completed"),)
        assert report.released_jobs == () and report.released == 0
        assert world.outcome(request.request_id).cause is TerminalCause.completed

        # unset, the bound is the generation budget: nothing in flight is cut short that
        # would not have hit its own deadline anyway
        bounds = []
        default = service_for(world, Answering(), reap_interval_s=3600)

        drain = default.loop.drain

        async def recording(within_s):
            bounds.append(within_s)
            return await drain(0.0)
        default.loop.drain = recording

        async def start_then_stop():
            # a stop before the pool's task has even run once (a SIGTERM during start-up):
            # stop() returns only once that pool has stopped too (review SC-1 / R7)
            await default.start()
            await default.stop()
            return default._pool.done(), (await default.readiness())["loop"]
        stopping = asyncio.create_task(start_then_stop())
        done, _ = await asyncio.wait({stopping}, timeout=5)
        assert done and bounds == [world.limits.generation_timeout_s]
        assert stopping.result() == (True, "stopped"), stopping.result()
    run(case())


CHILD = r"""
import asyncio, json, sys
# The child must start with SIGINT deliverable: a detached launcher (nohup/setsid, make
# api-mutants in the background) inherits SIGINT ignored, and the unmutated service installs
# its own handler anyway - only the sigint_not_handled mutant then differs (checkpoint 2).
import signal; signal.signal(signal.SIGINT, signal.default_int_handler)
from infrx.contracts.records import Usage
from infrx.worker import WorkerLoop, WorkerService
from tests.w.test_loop import PROGRESS, ScriptEngine, World, candidate, delta, queued, usage_event

class Slow(ScriptEngine):
    async def _events(self):
        yield PROGRESS
        await asyncio.sleep(float(sys.argv[1]))
        yield delta("drained ")
        yield usage_event(Usage.of(1200, 1))

async def main():
    world = World()
    request, _ = await queued(world)
    await world.scheduler.enqueue(candidate(world, request))
    loop = WorkerLoop(scheduler=world.scheduler, runner=world.runner(Slow()), limits=world.limits)
    service = WorkerService(loop=loop, jobs=world.jobs, engine=Slow(), drain_s=float(sys.argv[2]))
    async def announce():
        while not loop.in_flight:
            await asyncio.sleep(0.01)
        print("busy", flush=True)
    announcing = asyncio.get_running_loop().create_task(announce())
    report = await service.serve()
    print(json.dumps({"ended": report.ended, "released": report.released_jobs,
                      "state": world.jobs.jobs[request.request_id].state.value}), flush=True)

asyncio.run(main())
"""


def serve_and_terminate(answer_after_s: float, drain_s: float,
                        sig: int = signal.SIGTERM) -> tuple[int, dict]:
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(answer_after_s), str(drain_s)],
                             cwd=API, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env={**os.environ, "PYTHONPATH": str(API)})
    try:
        assert child.stdout.readline().strip() == "busy"
        child.send_signal(sig)
        out, _ = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    lines = out.strip().splitlines()
    return child.returncode, json.loads(lines[-1]) if lines else {}


def test_ops_recover__sigterm_drains_within_the_bound_and_exits_cleanly():
    """What a service manager does on stop: SIGTERM (and an operator's Ctrl-C: SIGINT).
    The process stops claiming, lets the attempt that fits the bound finish, releases the
    one that does not, reports both, and exits 0 - it is not killed mid-write by its own
    signal."""
    status, report = serve_and_terminate(answer_after_s=0.3, drain_s=10.0)
    assert status == 0 and report["state"] == "succeeded", report
    assert [cause for _, cause in report["ended"]] == ["completed"] and report["released"] == []
    status, report = serve_and_terminate(answer_after_s=60.0, drain_s=0.2, sig=signal.SIGINT)
    assert status == 0 and report["state"] == "running", report
    assert len(report["released"]) == 1 and report["ended"] == []


# --------------------------------------------------------------------------
# (4) readiness and liveness, protected
# --------------------------------------------------------------------------
def test_ops_recover__readiness_tells_engine_down_from_idle_from_busy_from_draining(
        monkeypatch):
    """`/readyz` is 200 only for an engine that answers ready and a pool that is running
    and not draining; the body names the reason. `/livez` stays 200 through a drain and
    turns 503 when the pool has died unasked. An engine that does not answer within
    `HEALTH_TIMEOUT_S` is down, not a hung probe."""
    async def case():
        world = World()
        request, _ = await queued(world)
        engine = Blocking()
        service = service_for(world, engine, drain_s=1.0, reap_interval_s=3600,
                              health_port=0)
        before = await service.readiness()
        assert (before["ready"], before["loop"], before["live"]) == (False, "stopped", False)
        await service.start()
        port = service._server.sockets[0].getsockname()[1]
        status, body = await http_get(port, "/readyz")
        assert (status, body["engine"], body["loop"], body["ready"]) == (200, "up", "idle", True)

        await world.scheduler.enqueue(candidate(world, request))
        assert await eventually(lambda: service.loop.in_flight)
        status, body = await http_get(port, "/readyz")
        assert (status, body["loop"], body["in_flight"]) == (200, "busy", 1)

        async def down():
            return {"ready": False}
        engine.health = down
        status, body = await http_get(port, "/readyz")
        assert (status, body["engine"], body["loop"]) == (503, "down", "busy")
        status, body = await http_get(port, "/livez")
        assert status == 200 and body["live"] is True

        async def raises():
            raise RuntimeError("the engine client is broken")
        engine.health = raises
        assert (await service.readiness())["engine"] == "down"

        async def hangs():
            await asyncio.Event().wait()
        engine.health = hangs
        monkeypatch.setattr(service_module, "HEALTH_TIMEOUT_S", 0.1)
        checking = asyncio.create_task(service.readiness())
        done, _ = await asyncio.wait({checking}, timeout=2.0)
        assert done, "a hung engine health check hung the readiness probe"
        assert checking.result()["engine"] == "down"

        async def up():
            return {"ready": True}
        engine.health = up
        stopping = asyncio.create_task(service.stop())
        assert await eventually(lambda: service.loop.draining)
        status, body = await http_get(port, "/readyz")
        assert (status, body["engine"], body["loop"]) == (503, "up", "draining")
        status, body = await http_get(port, "/livez")
        assert status == 200
        await stopping
        state = await service.readiness()
        # stopped because it was asked to: still live, nothing died
        assert (state["loop"], state["live"], state["reaper"]) == ("stopped", True, "stopped")

        # a pool whose every runner died is not live, and says so
        dead = World()
        service = service_for(dead, Answering(), reap_interval_s=3600)

        async def broken(*args, **kw):
            raise ConnectionError("the index is gone")
        dead.scheduler.claim_candidate = broken
        await service.start()
        assert await eventually(lambda: service._pool.done())
        state = await service.readiness()
        assert (state["live"], state["ready"], state["failures"]) == (False, False, 1)
        assert state["runners_dead"] == 1
        await service.stop()
        # and `serve` returns by itself rather than waiting for a signal that never comes
        again = service_for(dead, Answering(), reap_interval_s=3600)
        serving = asyncio.create_task(again.serve())
        done, _ = await asyncio.wait({serving}, timeout=5)
        serving.cancel()
        assert done and len(again.loop.failures) == 1
    run(case())


def one_claim_fails(world: World) -> list:
    """The index blinks once: the first `claim_candidate` raises, every later one works.
    Whichever runner asks first dies; the others live on."""
    claim, blinked = world.scheduler.claim_candidate, []

    async def blinks(*args, **kw):
        if not blinked:
            blinked.append(True)
            raise ConnectionError("the index blinked")
        return await claim(*args, **kw)
    world.scheduler.claim_candidate = blinks
    return blinked


def test_ops_recover__one_dead_runner_makes_the_worker_not_live_and_ends_serve(caplog):
    """A pool one runner short is not healthy: `/livez` and `/readyz` are 503 and say how
    many runners died, and `serve()` drains what still runs and returns, so the service
    manager restarts the process at full concurrency (review S1: it used to read 200 /
    200 / `failures` 0 for its whole life, and never exit). The death is logged with its
    cause - the runner's exception, or the pool's own when it cannot start a runner."""
    caplog.set_level(logging.ERROR, logger="infrx.worker")

    def deaths():
        return [(r.getMessage(), r.exc_info and r.exc_info[0]) for r in caplog.records
                if r.levelno == logging.ERROR and "died" in r.getMessage()]

    async def case():
        world = World()
        service = service_for(world, Answering(), concurrency=2, reap_interval_s=3600,
                              health_port=0)
        blinked = one_claim_fails(world)
        await service.start()
        port = service._server.sockets[0].getsockname()[1]
        assert await eventually(lambda: blinked)
        status, body = await http_get(port, "/livez")
        assert (status, body["live"], body["runners_dead"]) == (503, False, 1), body
        status, body = await http_get(port, "/readyz")
        assert (status, body["ready"], body["loop"]) == (503, False, "idle"), body
        await service.stop()

        again = World()
        service = service_for(again, Answering(), concurrency=2, reap_interval_s=3600)
        one_claim_fails(again)
        serving = asyncio.create_task(service.serve())
        done, _ = await asyncio.wait({serving}, timeout=5)
        assert done, "serve() waited for a pool that runs one runner short"
        # it drained the survivor on the way out rather than walking away from it
        assert serving.result() is service.last_drain and service.loop.draining
        assert len(service.loop.failures) == 1
        assert [cause for _, cause in deaths()] == [ConnectionError], deaths()
        assert deaths()[0][0].startswith("worker-w3-"), deaths()

        # a pool that cannot start its runners at all dies with its own cause, logged
        caplog.clear()
        broken = service_for(World(), Answering(), concurrency=0, reap_interval_s=3600)
        serving = asyncio.create_task(broken.serve())
        done, _ = await asyncio.wait({serving}, timeout=5)
        assert done and deaths() == [("pool died; draining for a restart", ValueError)], deaths()
    run(case())


def test_ops_recover__a_garbage_recover_does_not_kill_the_reaper_and_a_dead_one_is_not_live():
    """`recover` answering garbage (not a list) is counted like a store that is down, and
    the timer goes on. A reaper that dies anyway - a defect outside that guard - would
    leave every lost lease unreaped for good, so it is reported (`reaper: dead`), makes
    the worker not live, and ends `serve()` for a restart, as a dead runner does."""
    async def case():
        world = World()
        service = service_for(world, Answering(), reap_interval_s=0.02)
        await service.start()

        async def garbage():
            return None
        world.jobs.recover = garbage
        assert await eventually(lambda: service.reap_errors >= 2)
        state = await service.readiness()
        assert (state["reaper"], state["live"]) == ("running", True), state

        async def broken():
            raise RuntimeError("the reaper broke")
        service.reap_once = broken
        assert await eventually(lambda: service._reaper.done())
        state = await service.readiness()
        assert (state["reaper"], state["live"], state["ready"]) == ("dead", False, False), state
        assert state["runners_dead"] == 0, state          # a dead reaper is not a dead runner
        await service.stop()

        again = World()
        service = service_for(again, Answering(), reap_interval_s=0.02)
        reap, calls = service.reap_once, []

        async def breaks_on_its_timer():
            calls.append(True)
            if len(calls) > 1:
                raise RuntimeError("the reaper broke")
            return await reap()
        service.reap_once = breaks_on_its_timer
        serving = asyncio.create_task(service.serve())
        done, _ = await asyncio.wait({serving}, timeout=5)
        assert done, "serve() outlived its reaper"
        assert serving.result() is service.last_drain
    run(case())


def test_ops_recover__readiness_is_never_public_and_leaks_nothing():
    """Loopback only: any other bind is refused at construction, and again at start (the
    field is mutable), and the listener is bound to that address and no other. Only the
    two probe paths answer, only to GET, and the body carries counts - never a job id."""
    world = World()
    for host in ("0.0.0.0", "10.0.0.5", "::", "localhost", ""):
        with pytest.raises(ValueError):
            service_for(world, Answering(), health_host=host)
    assert service_for(world, Answering(), health_host="::1").health_host == "::1"

    async def case():
        request, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        # changed after construction: start refuses before it binds, reaps or claims
        moved = service_for(world, Blocking(), reap_interval_s=3600, health_port=0)
        moved.health_host = "0.0.0.0"
        with pytest.raises(ValueError):
            await moved.start()
        await asyncio.sleep(0.05)
        assert moved._server is None, "bound the public address before refusing it"
        assert moved.loop.claimed == 0 and moved._pool is None

        # a readiness port already taken: start fails before the pool claims anything, so
        # a restart loop on a busy port cannot orphan a claimed job each time round
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            busy = service_for(world, Blocking(), reap_interval_s=3600,
                               health_port=taken.getsockname()[1])
            with pytest.raises(OSError):
                await busy.start()
            await asyncio.sleep(0.05)
            assert busy.loop.claimed == 0 and busy._pool is None

        service = service_for(world, Blocking(), drain_s=0.01, reap_interval_s=3600,
                              health_port=0)
        await service.start()
        host, port = service._server.sockets[0].getsockname()[:2]
        assert host == "127.0.0.1", host
        assert await eventually(lambda: service.loop.in_flight)
        for method, path in (("GET", "/"), ("GET", "/metrics"), ("POST", "/readyz"),
                             ("GET", "/readyz/../v1")):
            status, body = await http_get(port, path, method)
            assert (status, body) == (404, {"error": "not_found"}), (method, path)
        await service.stop()
        assert service.last_drain.released_jobs == (request.request_id,)
        assert request.request_id not in json.dumps(await service.readiness())
    run(case())


# --------------------------------------------------------------------------
# (5) OPS-RECOVER on the integration engine (a real process, real HTTP)
# --------------------------------------------------------------------------
@pytest.fixture
def fake_vllm():
    module = module_at("infrx_w3_fake_vllm", REPO / "tests" / "integration" / "fake_vllm.py")
    servers = []

    def start(**kw):
        server = module.FakeVllmServer(free_port(), **kw).start()
        servers.append(server)
        return server
    yield start
    for server in servers:
        server.stop()


def real_engine(world: World, server) -> VllmEngine:
    return VllmEngine(httpx.AsyncClient(base_url=server.base_url), served_model=SERVED_MODEL,
                      clock=world.clock, limits=world.limits)


def test_ops_recover__on_the_integration_engine_a_released_attempt_completes_after_requeue(
        fake_vllm):
    """The drain drill on real HTTP: the engine process holds the request (a prefill that
    does not end), the drain releases it and closes the connection, nothing is settled,
    and past the TTL a successor's reaper requeues it and the same engine process answers
    generation 2 completely."""
    server = fake_vllm(fault="prefill_stall", stall_real_s=30.0)

    async def case():
        world = World()
        request, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        engine = real_engine(world, server)
        service = service_for(world, engine, drain_s=0.2, reap_interval_s=3600)
        await service.start()
        assert await eventually(lambda: server.control()["requests"] == 1)
        report = await service.stop()
        assert report.released_jobs == (request.request_id,)
        assert world.outcome(request.request_id) is None

        server.control(fault="none")
        world.clock.advance(world.limits.lease_ttl_s + 1)
        successor = service_for(world, engine, reap_interval_s=3600)
        await successor.start()
        assert await eventually(lambda: world.outcome(request.request_id) is not None)
        await successor.stop()
        outcome = world.outcome(request.request_id)
        assert (outcome.state, outcome.cause) == (JobState.succeeded, TerminalCause.completed)
        assert outcome.usage is not None and outcome.debit > 0
        assert [r.generation for r in successor.loop.results] == [2]
    run(case())


def test_ops_recover__engine_process_loss_is_a_typed_failure_and_readiness_follows_it(
        fake_vllm):
    """SIGKILL the engine: readiness says `engine: down` (503) while the pool stays live;
    an attempt against the dead process settles a typed failure with no charge rather than
    hanging or escaping untyped; a restarted engine on the same port is `up` again."""
    server = fake_vllm()

    async def case():
        world = World()
        engine = real_engine(world, server)
        service = service_for(world, engine, reap_interval_s=3600, health_port=0)
        await service.start()
        assert (await service.readiness())["engine"] == "up"
        assert server.kill() == -signal.SIGKILL
        state = await service.readiness()
        assert (state["ready"], state["engine"], state["live"]) == (False, "down", True)

        request, _ = await queued(world)
        await world.scheduler.enqueue(candidate(world, request))
        assert await eventually(lambda: world.outcome(request.request_id) is not None)
        outcome = world.outcome(request.request_id)
        assert (outcome.state, outcome.cause, outcome.debit) == (
            JobState.failed, TerminalCause.engine_error, 0)

        server.start()
        assert (await service.readiness())["engine"] == "up"
        await service.stop()
    run(case())


# --------------------------------------------------------------------------
# PERF-PILOT: the worker's phases, in the bench client's vocabulary
# --------------------------------------------------------------------------
def test_perf_pilot__an_attempt_times_its_phases_in_the_bench_vocabulary():
    """Prefill (to the first delta), generate (first delta to the end of the stream), the
    journal appends, the result object and the settling call - each measured on the
    injected clock, each named as `bench.py` expects a `Server-Timing` metric, and the
    header value parses back to the same numbers."""
    bench = module_at("infrx_w3_bench", REPO / "models" / "marlin2b" / "bench.py")

    async def case():
        world = World()
        request, _ = await queued(world)
        engine = ScriptEngine(events=(PROGRESS, delta("an "), delta("answer"),
                                      usage_event(Usage.of(1200, 2))),
                              clock=world.clock, advance_s=0.1)
        appended = []
        append = world.stream.append

        async def slow_append(lease, events):
            world.clock.advance(0.01)
            appended.append(len(events))
            return await append(lease, events)
        world.stream.append = slow_append

        async def slow_result(job_id, text, lease):
            world.clock.advance(0.25)
            return f"infrx-result:{job_id}"

        complete = world.jobs.complete

        async def slow_complete(lease, outcome):
            world.clock.advance(0.03)
            return await complete(lease, outcome)
        world.jobs.complete = slow_complete
        result = await world.runner(engine, put_result=slow_result).run(request.request_id)
        assert result.cause is TerminalCause.completed
        # prefill: two 100 ms events to the first delta; generate: two more events plus
        # every append (each 10 ms) from the first delta on; then the result and the settle
        journal = 10.0 * len(appended)
        assert result.timings == {"prefill": 200.0, "journal": journal,
                                  "generate": 200.0 + journal, "persist": 250.0,
                                  "settle": 30.0}, result.timings
        assert set(result.timings) == set(TIMED_PHASES) <= set(bench.PHASES)
        assert bench.parse_server_timing(server_timing(result.timings)) == result.timings

        # a stream that never produced a delta has no prefill or generate - absent, not zero
        silent = World()
        request, _ = await queued(silent)
        nothing = await silent.runner(ScriptEngine(events=(usage_event(Usage.of(1200, 0)),))
                                      ).run(request.request_id)
        assert not {"prefill", "generate"} & set(nothing.timings)
    run(case())
