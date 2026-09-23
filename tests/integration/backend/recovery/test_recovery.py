"""I3B.b: recovery drills as executable cases - OPS-RECOVER, BACKEND-OBSERVE, DUR-OUTBOX.

Every drill injects one loss, lets the system recover the way production will (a new
process, the reaper's `JobStore.recover()`, an index rebuilt from the durable snapshot),
then ends in `World.reconcile()`: no accepted job lost, pins unchanged, holds equal to what
is still held, exactly one terminal usage projection per job, a debit only when settled,
and a succeeded job's output from exactly one generation. Each also checks that the fault
is visible in the metrics and that the alert its runbook answers fires.

What is real in each drill, and what stands in for a component that is missing:

| drill | real | stand-in (pending the task named) |
|---|---|---|
| rc01 worker loss | W2 loop/runner | store: reference fake (D2/D3); the kill is a task cancellation |
| rc02 engine loss | a separate engine **process**, SIGKILLed; E2's HTTP adapter | store (D2-D5) |
| rc03 gateway restart | - | PENDING G1R, G2 (route); the store half is E3B dr01 |
| rc04 database loss | - | PENDING D2, D3 here; the PostgreSQL half is `test_restore.py` bk03 |
| rc05 object store | M2's preparation; an outage in front of the object store | PENDING M3 for MinIO |
| rc06 index loss | Q2's `ValkeyScheduler` on E2's Valkey, SIGKILLed | snapshot from the fake (Q3) |
| rc07 disk full | a 256 KiB tmpfs under M2's processing cache | store (D2-D5) |
| rc08 drain | W2's drain over real Valkey | PENDING W3 for the process's SIGTERM path |
| rc09 host loss | engine process + Valkey + worker, all at once | store survives (hosted, D2-D5) |
| rc10 rollback | - | PENDING I2B (its rollback script) |

The emulated glue (dispatcher, preparation worker, reaper tick) is `recoverykit.World`.
A drill that passes on a stand-in is *implemented*, never *integrated*.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import recoverykit as kit                               # noqa: E402

import harness                                          # noqa: E402
import stack                                            # noqa: E402

from infrx.contracts.limits import DEFAULTS             # noqa: E402
from infrx.contracts.records import (ChunkEventType, EngineEvent, JobState,  # noqa: E402
                                     TerminalCause)
from infrx.media import prepare, store                  # noqa: E402
from infrx.observe import host, metrics                 # noqa: E402

PROGRESS = EngineEvent(type=ChunkEventType.progress, payload={"phase": "running"})


class Hanging:
    """An engine that starts, publishes on its first stream only, then never answers: the
    shape of a worker (or host) dying mid-generation."""

    def __init__(self, clock, *, publish: bool = True) -> None:
        self.clock, self.publish = clock, publish
        self.running: list[str] = []
        self.published: str | None = None

    def generate(self, lease, prepared):
        return self._events(lease)

    async def _events(self, lease):
        yield PROGRESS
        if self.publish and self.published is None:
            self.published = str(lease.job_id)
            yield kit.delta("seen ")
            self.clock.advance(0.1)                 # past STREAM_BATCH_MS: the batch commits
            yield kit.delta("by the customer ")
        self.running.append(str(lease.job_id))
        await asyncio.Event().wait()

    async def cancel(self, lease) -> bool:
        return True

    async def health(self) -> dict:
        return {"ready": True}

    async def drain(self) -> None:
        return None


class Watched:
    """Any engine, noting which jobs' streams have produced a delta."""

    def __init__(self, inner) -> None:
        self.inner, self.streaming, self.first_delta = inner, set(), asyncio.Event()

    def generate(self, lease, prepared):
        return self._events(lease, prepared)

    async def _events(self, lease, prepared):
        async for event in self.inner.generate(lease, prepared):
            if event.type is ChunkEventType.delta:
                self.streaming.add(str(lease.job_id))
                self.first_delta.set()
            yield event

    async def cancel(self, lease) -> bool:
        return await self.inner.cancel(lease)

    async def health(self) -> dict:
        return await self.inner.health()

    async def drain(self) -> None:
        await self.inner.drain()


async def until(predicate, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("the drill never reached its injection point")
        await asyncio.sleep(0.01)


async def kill(task: asyncio.Task) -> None:
    """The worker process is gone: no drain, no settle, nothing after this point runs."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def probe(world, component: str, check) -> bool:
    """A readiness probe as W3's `/readyz` will run it, recorded as `infrx_component_up`."""
    try:
        await check()
        up = True
    except Exception:                                # noqa: BLE001 - down is the answer
        up = False
    world.metrics.set("infrx_component_up", 1 if up else 0, component=component)
    return up


def fake_vllm():
    import fake_vllm as module                      # tests/integration, on stack's path
    return module


# ------------------------------------------------------------------ worker, engine, gateway

def test_i3b_rc01_a_worker_lost_mid_attempt_loses_no_accepted_job():
    """OPS-RECOVER (worker restart). Four accepted jobs, two tenants; the worker dies holding
    two leases, one of which had already published output. After the lease TTL the reaper
    requeues the prepublication attempt as a new generation and fails the published one
    honestly (`lost_after_publication`, usage unknown, hold kept); a restarted worker finishes
    the rest. The 24 h unknown-usage window then releases the kept hold."""
    world = kit.World()

    async def body():
        jobs = [await world.queued(n % 2) for n in range(4)]
        await world.dispatch(*(job.request_id for job in jobs))
        engine = Hanging(world.clock)
        dead = asyncio.create_task(world.loop(engine, "worker-dead").run(
            concurrency=2, stop_when_idle=False))
        await until(lambda: len(engine.running) == 2)
        before = world.scrape()
        await kill(dead)

        world.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await world.reap()
        assert len(produced) == 2, produced
        await world.finish(world.loop(worker_id="worker-b"))
        summary = await world.reconcile(allow_unknown=1)
        assert summary["terminal"] == {"succeeded/completed": 3,
                                       "failed/lost_after_publication": 1}, summary
        published = world.jobs.jobs[engine.published].outcome
        assert published.cause is TerminalCause.lost_after_publication
        assert {"LeaseLost", "ReaperTerminalized"} <= kit.fired(world, before)

        world.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
        await world.reap()
        assert (await world.reconcile())["held_unknown"] == 0
    kit.run(body)


def test_i3b_rc02_an_engine_process_killed_mid_stream_costs_nothing_and_a_new_one_serves():
    """OPS-RECOVER (engine restart), with a real engine PROCESS (E2's fake vLLM) and E2's HTTP
    adapter. SIGKILL after the first delta: the attempt fails `platform_error`, free,
    before anything was published; the engine probe goes down (ComponentDown fires); a
    restarted engine serves the other two jobs; the failure rate alert sees 1 in 3."""
    world = kit.World()
    port = kit.free_port()
    servers = []

    def start(**kw):
        server = fake_vllm().FakeVllmServer(port, **kw).start()
        servers.append(server)
        return server

    async def body():
        stalled = start(fault="midstream_stall", stall_real_s=60.0)
        jobs = [await world.queued(n % 2) for n in range(3)]
        await world.dispatch(*(job.request_id for job in jobs[1:]))
        # The request body names the fault (it beats the server's default), and the server
        # really sleeps inside it, so the SIGKILL lands mid-stream.
        engine = fake_vllm().HttpEngine(stalled.base_url, clock=world.clock, timeout=30.0,
                                        fault="midstream_stall")
        watched = Watched(engine)
        before = world.scrape()
        assert await probe(world, "engine", engine.health)
        attempt = asyncio.create_task(world.runner(watched).run(jobs[0].request_id))
        await asyncio.wait_for(watched.first_delta.wait(), 15)
        stalled.kill()
        result = await asyncio.wait_for(attempt, 30)
        metrics.record_outcome(world.metrics, result.outcome)
        assert result.outcome.cause is TerminalCause.platform_error
        assert result.outcome.debit == 0 and result.committed == 0
        assert not await probe(world, "engine", engine.health)
        assert "ComponentDown" in kit.fired(world, before)

        start(fault="none")
        engine = fake_vllm().HttpEngine(stalled.base_url, clock=world.clock, timeout=30.0)
        assert await probe(world, "engine", engine.health)
        await world.finish(world.loop(engine, "worker-b"))
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 2, "failed/platform_error": 1}
        fired = kit.fired(world, before)
        assert "PlatformFailureRate" in fired and "ComponentDown" not in fired
    try:
        kit.run(body)
    finally:
        for server in servers:
            server.stop()


def test_i3b_rc03_a_gateway_restart_is_pending_on_the_metered_route():
    """OPS-RECOVER (gateway restart): a restart between durable acceptance and the answer
    must replay the same accepted identity, and a restart mid-stream must leave the job to
    the worker. The store half is E3B's dr01 (crash after commit, idempotent retry); the
    route half needs the mounted pilot ingress, and fails the day it is mounted."""
    if stack.ingress_is_mounted():
        pytest.fail("the pilot ingress is mounted: write the gateway restart drill body now")
    kit.pending("G1R", "G2", why="no metered route is mounted to restart under load")


def test_i3b_rc04_a_database_loss_under_the_job_store_is_pending_on_its_adapter():
    """OPS-RECOVER (DB interruption) with the product's store: admission, claim and settle
    across a PostgreSQL restart. Needs the PostgreSQL JobStore; the RPCs are still stubs,
    and the day they are not this fails. What the database itself guarantees across a
    SIGKILL is measured now, in `test_restore.py` bk03."""
    stubs = stack.unimplemented_rpcs()
    if stubs == 0:
        pytest.fail("the D RPCs are implemented: drive the DB loss through the real adapter")
    kit.pending("D2", "D3", why=f"{stubs} infrx RPCs are still infrx.unimplemented stubs")


# ------------------------------------------------------------------ media: object store, disk

class Outage(store.InMemoryObjectStore):
    """The object store behind an outage switch: while `down`, every call fails the way an
    unreachable S3 endpoint does."""

    down = False

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("object store unreachable")

    async def head(self, key):
        self._check()
        return await super().head(key)

    async def get(self, key):
        self._check()
        return await super().get(key)

    async def put_if_absent(self, key, data, content_type):
        self._check()
        return await super().put_if_absent(key, data, content_type)


def with_media(world, objects, cache_root: str) -> None:
    world.media = prepare.MediaPreparation(
        objects, cache=prepare.ProcessingCache(cache_root),
        job_org=lambda job_id: world.accepted[job_id].org_id)


async def video_job(world, tenant: int, clip: bytes):
    """A video job accepted while the object store was healthy: materialized, admitted,
    attached - the state `stage`/`attach` leave before preparation."""
    org = kit.TENANTS[tenant][0]
    ref = await world.media.materialize(
        org, "data:video/mp4;base64," + base64.b64encode(clip).decode())
    admission = await world.admit(tenant, refs=(ref,), mode=kit.ExecutionMode.async_)
    await world.media.attach(admission.request_id, (ref,))
    return admission


async def prepare_until_settled(world, job_id: str) -> bool:
    """Preparation attempts, each followed by the lease TTL and a reaper tick, until the job
    is queued (True) or the store gives up (False). Bounded by the retry budget."""
    for attempt in range(DEFAULTS.max_prepublication_retries + 2):
        if await world.prepare(job_id, f"prep-{attempt}"):
            return True
        world.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        await world.reap()
        if world.jobs.jobs[job_id].state is not JobState.preparing:
            return False
    raise AssertionError("preparation neither succeeded nor was given up")


def test_i3b_rc05_an_object_store_outage_during_preparation_is_retried_or_released(tmp_path):
    """OPS-RECOVER (object-store interruption), through M2's real preparation code. Job 1's
    outage is shorter than its retry budget: preparation fails, the reaper re-dispatches it
    and the job completes once the store is back. Job 2's outage outlasts the budget: the
    reaper fails it `preparation_failed`, free, hold released. The probe shows the store
    down while it is."""
    world = kit.World()
    objects = Outage()
    with_media(world, objects, str(tmp_path / "cache"))
    clip = kit.m_support().mp4(seconds=10.0)

    async def body():
        short, long = await video_job(world, 0, clip), await video_job(world, 1, clip)
        before = world.scrape()
        objects.down = True                          # a short outage
        assert not await probe(world, "object_store", lambda: objects.head("probe"))
        assert not await world.prepare(short.request_id, "prep-a")
        assert "ComponentDown" in kit.fired(world, before)
        world.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        await world.reap()
        objects.down = False
        assert await probe(world, "object_store", lambda: objects.head("probe"))
        assert await world.prepare(short.request_id, "prep-b")
        await world.dispatch(short.request_id)
        await world.finish(world.loop())
        objects.down = True                          # an outage longer than the budget
        assert not await prepare_until_settled(world, long.request_id)
        objects.down = False
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 1,
                                       "failed/preparation_failed": 1}, summary
        assert "ReaperTerminalized" in kit.fired(world, before)
    kit.run(body)


def _object_store_adapters() -> list[str]:
    """D3: structural, not a name heuristic - every class defined in an `infrx.media` module
    that has the port's core operations, other than the Protocol and the in-memory double.
    ponytail: a module that fails to import (a missing optional dependency) is not seen."""
    import inspect
    import pkgutil

    import infrx.media
    core = ("head", "get", "put_if_absent")
    found = []
    for info in pkgutil.iter_modules(infrx.media.__path__, "infrx.media."):
        try:
            module = importlib.import_module(info.name)
        except ImportError:
            continue
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ \
                    and cls not in (store.ObjectStore, store.InMemoryObjectStore) \
                    and all(callable(getattr(cls, op, None)) for op in core):
                found.append(f"{module.__name__}.{name}")
    return found


def test_i3b_rc05b_an_object_store_outage_on_minio_is_pending_on_the_s3_adapter():
    """The same drill against E2's MinIO needs an S3-backed `ObjectStore`; `media.store`
    has only the in-memory one. Fails the day any class in `infrx.media` implements the
    port (whatever its name) other than the in-memory double."""
    names = _object_store_adapters()
    if names:
        pytest.fail(f"an S3 object store exists ({names}): pause MinIO under it now")
    kit.pending("M3", why="no S3-backed ObjectStore in infrx.media (InMemoryObjectStore only)")


@contextlib.contextmanager
def small_tmpfs(size: str = "256k"):
    """A size-limited tmpfs, the brief's "disk exhaustion via a small tmpfs". Mounting needs
    privilege: passwordless sudo on this host, recorded in the evidence. No sudo is a
    failure of the layer-3 gate, not a silent skip."""
    mountpoint = tempfile.mkdtemp(prefix="infrx-i3b-tmpfs-")
    mount = subprocess.run(["sudo", "-n", "mount", "-t", "tmpfs", "-o",
                            f"size={size},mode=0777", "tmpfs", mountpoint],
                           capture_output=True, text=True, timeout=30)
    if mount.returncode != 0:
        os.rmdir(mountpoint)
        pytest.fail(f"cannot mount a {size} tmpfs (needs passwordless sudo): "
                    f"{mount.stderr.strip()[:200]}")
    try:
        yield mountpoint
    finally:
        subprocess.run(["sudo", "-n", "umount", mountpoint], capture_output=True, timeout=30)
        os.rmdir(mountpoint)


def fill(path: str) -> int:
    """Write until the filesystem says ENOSPC. Returns the bytes written."""
    written, descriptor = 0, os.open(os.path.join(path, "filler"), os.O_WRONLY | os.O_CREAT)
    try:
        while True:
            written += os.write(descriptor, b"\0" * 4096)
    except OSError as full:
        assert full.errno == errno.ENOSPC, full
    finally:
        os.close(descriptor)
    return written


def test_i3b_rc07_a_full_processing_cache_disk_fails_preparation_free_and_recovers(record_property):
    """OPS-RECOVER / BACKEND-OBSERVE (disk exhaustion), on a real full filesystem under M2's
    processing cache. The disk gauge reads ~0 free and DiskAlmostFull fires; preparation
    cannot write, the reaper retries within budget then fails the job `preparation_failed`,
    free, hold released; with space freed a new job prepares and completes."""
    kit.needs_stack()
    world = kit.World()
    clip = kit.m_support().mp4(seconds=10.0)
    with small_tmpfs() as root:
        with_media(world, store.InMemoryObjectStore(), root)

        async def body():
            before = world.scrape()
            fill(root)
            host.collect_disks(world.metrics, {"media": root})
            assert world.metrics.value("infrx_disk_free_ratio", mount="media") < 0.05
            assert "DiskAlmostFull" in kit.fired(world, before)
            starved = await video_job(world, 0, clip)
            assert not await prepare_until_settled(world, starved.request_id)
            leftovers = sorted(name for _, _, files in os.walk(root) for name in files
                               if name.endswith(".part"))
            # A finding for M (integration request): a failed cache write leaves its
            # temporary file on the full disk. Recorded, not asserted either way.
            record_property("part_files_left_on_full_disk", len(leftovers))

            os.remove(os.path.join(root, "filler"))
            host.collect_disks(world.metrics, {"media": root})
            recovered = world.scrape()
            assert world.metrics.value("infrx_disk_free_ratio", mount="media") > 0.5
            fed = await video_job(world, 1, clip)
            assert await world.prepare(fed.request_id, "prep-ok")
            await world.dispatch(fed.request_id)
            await world.finish(world.loop())
            summary = await world.reconcile()
            assert summary["terminal"] == {"succeeded/completed": 1,
                                           "failed/preparation_failed": 1}, summary
            assert "DiskAlmostFull" not in kit.fired(world, recovered)
        kit.run(body)


# ------------------------------------------------------------------ the index (real Valkey)

@pytest.fixture
def valkey_world():
    """A World whose index is Q2's `ValkeyScheduler` on E2's Valkey, in a namespace of our
    own under E2's prefix, removed afterwards."""
    kit.needs_stack()
    from valkey.asyncio import Valkey

    from infrx.scheduling.valkey import ValkeyScheduler
    namespace = f"{harness.VALKEY_PREFIX}{{i3b-{uuid.uuid4().hex}}}"
    world = kit.World()
    client = Valkey.from_url(harness.valkey_url())
    world.scheduler = ValkeyScheduler(client, world.clock.now, limits=DEFAULTS,
                                      namespace=namespace)
    world.valkey = client
    yield world
    harness.wait_valkey()
    sync = harness.valkey_client()
    leftovers = list(sync.scan_iter(f"{namespace}*"))
    if leftovers:
        sync.delete(*leftovers)


async def valkey_back(world) -> None:
    """After the container restarted: wait, drop the dead connections, prove the loss."""
    harness.wait_valkey()
    await world.valkey.connection_pool.disconnect()
    assert await world.scheduler.depth() == 0, "the index survived a SIGKILL: not a loss drill"


def test_i3b_rc06_losing_the_index_mid_operation_loses_no_accepted_job(valkey_world):
    """DUR-OUTBOX / OPS-RECOVER (queue-index loss), on the real Valkey through Q2's adapter,
    BETWEEN worker runs (no claim is in flight at the kill instant; rc09 covers a lease held
    across the kill). Six accepted jobs; two finish; Valkey is SIGKILLed; the next
    worker's claim fails loudly (a loop failure, and the index probe goes down) rather than
    idling as if the queue were empty; Valkey restarts empty; the index is rebuilt from the
    durable snapshot and every remaining job runs exactly once."""
    world = valkey_world

    async def body():
        jobs = [await world.queued(n % 2) for n in range(6)]
        await world.dispatch(*(job.request_id for job in jobs))
        first = world.loop(worker_id="worker-a")
        await first.run(concurrency=1, stop_when_idle=True, max_claims=2)
        assert len(first.results) == 2
        before = world.scrape()
        with harness.Faults() as faults:
            faults.kill_container("valkey")
            blind = world.loop(worker_id="worker-blind")
            await asyncio.wait_for(blind.run(concurrency=1, stop_when_idle=True), 60)
            assert blind.failures and blind.claimed == 0, "an index loss must not look idle"
            assert not await probe(world, "index", world.scheduler.depth)
        assert "ComponentDown" in kit.fired(world, before)
        await valkey_back(world)
        assert await probe(world, "index", world.scheduler.depth)
        assert await world.scheduler.rebuild(world.snapshot()) == 4
        rest = await world.finish(world.loop(worker_id="worker-b"))
        done = {result.job_id for result in first.results}
        assert sorted(result.job_id for result in rest) == \
            sorted(job.request_id for job in jobs if job.request_id not in done)
        for result in first.results:
            metrics.record_outcome(world.metrics, result.outcome)
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 6}, summary
    kit.run(body)


def test_i3b_rc08_a_drain_releases_in_flight_work_to_the_store_and_keeps_the_queue(valkey_world):
    """OPS-RECOVER (drain), W2's drain over the real index: the draining worker stops
    claiming, releases the attempt it cannot finish (settling nothing), and the other
    candidates stay in the index; after the lease TTL the reaper requeues the released job
    and a new worker finishes all three."""
    world = valkey_world

    async def body():
        jobs = [await world.queued(n % 2) for n in range(3)]
        await world.dispatch(*(job.request_id for job in jobs))
        engine = Hanging(world.clock, publish=False)
        draining = world.loop(engine, "worker-draining")
        running = asyncio.create_task(draining.run(concurrency=1, stop_when_idle=False))
        await until(lambda: len(engine.running) == 1)
        report = await asyncio.wait_for(draining.drain(within_s=0.05), 5)
        await asyncio.wait_for(running, 5)
        assert (report.released, report.claimed) == (1, 1)
        assert await world.scheduler.depth() == 2
        released = engine.running[0]
        assert world.jobs.jobs[released].outcome is None
        world.clock.advance(DEFAULTS.lease_ttl_s + 1)
        assert len(await world.reap()) == 1
        await world.finish(world.loop(worker_id="worker-b"))
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 3}, summary
    kit.run(body)


def test_i3b_rc08b_a_sigterm_drain_of_the_worker_process_is_pending_on_w3():
    """The same drain driven by SIGTERM to the worker PROCESS, bounded by the unit's
    `TimeoutStopSec`: needs W3's worker entry point (and I2B's unit). Fails the day
    `infrx.worker` grows one, in any of the shapes a process can start from here: an
    `infrx.worker.__main__` module (`python -m infrx.worker`), a `main` attribute of the
    package, or a worker module with an `if __name__ == "__main__":` guard. A console
    script is not a shape: `infrx-api` is never installed (`[tool.uv] package = false`), so
    no entry point of it can exist in the environment."""
    import re

    import infrx.worker as worker
    entry = importlib.util.find_spec("infrx.worker.__main__") or getattr(worker, "main", None) \
        or [path.name for path in Path(worker.__file__).parent.glob("*.py")
            if re.search(r"^if __name__ == .__main__.:", path.read_text(), re.M)]
    if entry:
        pytest.fail("infrx.worker has a process entry point: SIGTERM it mid-attempt now")
    kit.pending("W3", "I2B", why="no worker process entry point or unit to SIGTERM")


def test_i3b_rc09_a_host_loss_takes_engine_index_and_worker_and_loses_no_accepted_job(
        valkey_world):
    """OPS-RECOVER (host restart), the box's single points of failure at once: the worker
    process dies holding two leases mid-stream, the engine process is SIGKILLed and Valkey
    is SIGKILLed. The store (hosted PostgreSQL) survives. On the way back: engine restarted,
    index rebuilt from the durable snapshot, the reaper requeues the two prepublication
    attempts, a new worker finishes all four. Single-GPU process recovery, not HA (P-16)."""
    world = valkey_world
    port = kit.free_port()
    servers = []

    def start(**kw):
        server = fake_vllm().FakeVllmServer(port, **kw).start()
        servers.append(server)
        return server

    async def body():
        engine_process = start(fault="midstream_stall", stall_real_s=60.0)
        jobs = [await world.queued(n % 2) for n in range(4)]
        await world.dispatch(*(job.request_id for job in jobs))
        engine = fake_vllm().HttpEngine(engine_process.base_url, clock=world.clock,
                                        timeout=30.0, fault="midstream_stall")
        watched = Watched(engine)
        worker = asyncio.create_task(world.loop(watched, "worker-dead").run(
            concurrency=2, stop_when_idle=False))
        await until(lambda: len(watched.streaming) == 2, 15)
        before = world.scrape()
        with harness.Faults() as faults:
            await kill(worker)                  # everything on the host dies together
            engine_process.kill()
            faults.kill_container("valkey")
        await valkey_back(world)
        start(fault="none")
        engine = fake_vllm().HttpEngine(engine_process.base_url, clock=world.clock,
                                        timeout=30.0)
        assert await world.scheduler.rebuild(world.snapshot()) == 2
        world.clock.advance(DEFAULTS.lease_ttl_s + 1)
        assert len(await world.reap()) == 2
        await world.finish(world.loop(engine, "worker-b"))
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 4}, summary
        assert "LeaseLost" in kit.fired(world, before)
    try:
        kit.run(body)
    finally:
        for server in servers:
            server.stop()


# ------------------------------------------------------------------ rollout

def test_i3b_rc10_a_rollout_rollback_is_pending_on_the_deploy_scripts():
    """OPS-RECOVER (rollout rollback): roll the runtime back with admission paused and jobs
    drained/fenced, never onto the unmetered legacy runtime once CREDIT is enabled
    (maintenance 503 instead). The scripts are I2B's; this fails the day they exist."""
    deploy = harness.REPO_ROOT / "apps" / "infrx-api" / "deploy"
    scripts = sorted(path.name for path in deploy.glob("rollback*"))
    if scripts:
        pytest.fail(f"I2B's rollback exists ({scripts}): drive it through a rollback drill now")
    kit.pending("I2B", why="no rollback script in apps/infrx-api/deploy")
