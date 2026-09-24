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
| rc03 gateway restart | the mounted gateway PROCESS (E3B's `pilotbox`), SIGKILLed mid-answer and restarted, beside a worker process that survives it | text (E3B's journeys run video on the same two processes); the store half is E3B dr01 |
| rc04 database loss | rc04a: PgJobStore on PostgreSQL (D harness or E2's), SIGKILLed and restarted after a claim; rc04b: settlement across the kill (D5's terminalize, E3B phase 3) | the database's own boundary is `test_restore.py` bk03 |
| rc05 object store | M2's preparation; an outage in front of the object store; rc05b: M1-L2's S3ObjectStore on E2's MinIO, partitioned from the stack's network | - |
| rc06 index loss | Q2's `ValkeyScheduler` on E2's Valkey, SIGKILLed | snapshot from the fake (Q3) |
| rc07 disk full | a 256 KiB tmpfs under M2's processing cache | store (D2-D5) |
| rc08 drain | W2's drain over real Valkey | rc08b: PENDING I2B-R4 (the worker's `__main__`) for the process's SIGTERM path |
| rc09 host loss | engine process + Valkey + worker, all at once | store survives (hosted, D2-D5) |
| rc10/rc10b/rc10c rollback | I2B's `rollback.sh` (bash, unmodified) on a sandbox root; W2 drain; the reaper; bk04's maintenance on PostgreSQL | systemctl/docker/curl stubs; the restored runtime is an in-process WorkerLoop; store (D2-D5) |

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
from decimal import Decimal
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


def test_i3b_rc03_a_gateway_restart_leaves_the_job_to_the_worker_and_replays_its_identity(
        tmp_path):
    """OPS-RECOVER (gateway restart), on the mounted gateway process (E3B phase 3: the cutover
    mounted G2's relay; `pilotbox` runs it, with the worker in a process of its own). The
    gateway is SIGKILLed while an SSE answer streams (after the identity frame and a chunk)
    and while a sync client waits on a running job, and restarted at once. The SAME key is
    retried WHILE the job is still in flight (review J9: R91's in-flight replay across the
    restart - the new process never staged it) and replays the SAME accepted identity - the
    sync retry waits for and answers the committed result, the SSE retry streams the journal
    from the start to `[DONE]` - with `Idempotency-Replayed: true`; the worker that survived
    the kill (the same pid) finishes each job, one job per key, one debit each, no attempt
    left unreleased."""
    import signal

    import pilotbox
    kit.needs_stack()
    with pilotbox.journey(tmp_path) as trip:
        alpha = trip.world.alpha
        before = trip.wallet(alpha)
        worker = trip.box.processes["worker"].pid
        text = "Two people unload boxes from a van onto a trolley. " * 12
        trip.engine.control(text=text, delta_gap_s=0.1)
        messages = [{"role": "user", "content": "Describe the van."}]
        for mode in ("sse", "sync"):
            key = f"rc03-{mode}"
            sock = _raw_chat(trip.box.port, alpha.secret, messages, key, stream=mode == "sse")
            request_id, = _until(lambda: trip.db(
                "select request_id::text from infrx.jobs where idempotency_key = %s and "
                "published", key))[0]
            if mode == "sse":
                assert b"infrx.progress" in sock.recv(65536), "no identity frame before the kill"
            trip.box.stop("gateway", signal.SIGKILL)            # the gateway dies mid-answer
            sock.close()
            trip.box.start("gateway")                           # the restart
            assert trip.db("select settled_at from infrx.jobs where request_id = %s",
                           request_id) == [(None,)], "the job ended before the retry"
            again = trip.send(alpha, mode, messages, key)       # while it is in flight
            assert (again.status_code, again.headers["inference-id"],
                    again.headers.get("idempotency-replayed")) == (200, request_id, "true"), \
                again.text
            if mode == "sse":
                sent = pilotbox.frames(again.text)
                assert pilotbox.frame_data(sent[-1]) == "[DONE]", sent[-3:]
                content = "".join(choice["delta"].get("content", "") for frame in sent[1:-1]
                                  for choice in (pilotbox.frame_data(frame) or {})
                                  .get("choices", []))
            else:
                content = again.json()["choices"][0]["message"]["content"]
            assert content == text.strip() or content == text, content[-80:]
            assert _until(lambda: trip.db("select state, outcome_cause from infrx.jobs where "
                                          "request_id = %s and settled_at is not null",
                                          request_id)) == [("succeeded", "completed")]
            assert trip.db("select count(*) from infrx.jobs where idempotency_key = %s",
                           key) == [(1,)]
            assert trip.db("select count(*) from infrx.credit_ledger where request_id = %s",
                           request_id) == [(1,)], "not ONE debit for the job"
            assert trip.db("select count(*) from infrx.attempts where job_id = %s and "
                           "released_at is null", request_id) == [(0,)]
        assert trip.box.processes["worker"].pid == worker, "the worker did not survive"
        trip.conserved(alpha)
        assert trip.wallet(alpha)[0] < before[0]


def _raw_chat(port: int, secret: str, messages, key: str, *, stream: bool):
    """A chat request on a raw socket (the client the kill leaves behind)."""
    import json
    import socket
    body = {"model": stack.CREDIT_ALIAS, "messages": messages}
    payload = json.dumps({**body, "stream": True} if stream else body).encode()
    sock = socket.create_connection(("127.0.0.1", port), timeout=30)
    sock.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                 + f"Authorization: Bearer {secret}\r\nIdempotency-Key: {key}\r\n".encode()
                 + b"Content-Type: application/json\r\n"
                 + f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
    return sock


def _until(predicate, timeout: float = 60.0):
    end = time.monotonic() + timeout
    while True:
        found = predicate()
        if found or time.monotonic() > end:
            assert found, f"not within {timeout}s"
            return found
        time.sleep(0.05)


def test_i3b_rc04a_admission_and_a_claim_survive_a_postgresql_kill_under_pgjobstore(
        monkeypatch, record_property):
    """OPS-RECOVER (DB interruption) through the product's store (DRL-3): D's PgJobStore on a
    migrated PostgreSQL (the D harness, or E2's), and PostgreSQL SIGKILLed and restarted
    after a claim. The store object built before the kill is used after it - PgJobStore's
    connector opens a connection per operation, so none is open across the kill (a pooled
    store, DATABASE_POOL_*, is not covered): the claimed job is still running with its pins
    and the queued one still queued, a replayed admission is the same job, a second claim is
    fenced, a new admission is accepted and prepared, the reaper requeues the claimed job
    after the lease TTL and the next claim is generation 2; the wallet's summary reserves
    exactly the three holds and debits nothing, and it agrees with the ledger and the holds
    themselves (`infrx.wallet_reconciliation`: zero drift, and the runbook's detector finds
    none - and names ORG_A once a hold is released behind the summary's back). Settling
    across the loss is rc04b's."""
    import pgstate

    import test_restore as bk
    from infrx.contracts import errors
    from infrx.contracts.conformance import builders as b
    from infrx.contracts.records import ExecutionMode, IndexEvent
    from infrx.state import migrations as d_migrations
    from infrx.state import pgtesting
    monkeypatch.setenv("PGPASSWORD", bk.pg_password())
    started = "select pg_postmaster_start_time()"

    def request(h):          # async: a 10 s interactive queue budget is not this drill's case
        return b.request(h, mode=ExecutionMode.async_, max_output_tokens=256)

    async def body(database: str, factory) -> None:
        h = factory()
        h.extra["grant"](b.ORG_A, kit.GRANT)
        store = h.port
        first_request = request(h)
        first = await store.admit(first_request, b.idem(first_request, "rc04a-1"))
        second_request = request(h)
        second = await store.admit(second_request, b.idem(second_request, "rc04a-2"))
        for admission in (first, second):
            lease = await store.claim_preparation(admission.request_id, "prep-a")
            await store.prepared(lease, ())
        claimed = await store.claim(first.request_id, "worker-a")
        with bk.connect(database) as conn:
            before = conn.execute(started).fetchone()[0]

        record_property("postgres_kill_to_connection_s", round(bk.kill_postgres(database), 2))

        with bk.connect(database) as conn:
            assert conn.execute(started).fetchone()[0] > before, "PostgreSQL was not restarted"
        rig = factory()                 # the hooks' and the clock's own connection, anew
        replay = await store.admit(first_request, b.idem(first_request, "rc04a-1"))
        assert (replay.request_id, replay.job_handle) == (first.request_id, first.job_handle)
        for admission, state in ((first, JobState.running), (second, JobState.queued)):
            now, outcome = await store.get_owned(b.ORG_A, admission.job_handle)
            assert (now.state, outcome) == (state, None), (admission.request_id, now.state)
            for pin in ("price_snapshot", "maximum_hold", "deadline_at", "budgets"):
                assert getattr(now, pin) == getattr(admission, pin), (admission.request_id, pin)
        with pytest.raises(errors.NotClaimable):
            await store.claim(first.request_id, "worker-b")
        third_request = request(h)
        third = await store.admit(third_request, b.idem(third_request, "rc04a-3"))
        lease = await store.claim_preparation(third.request_id, "prep-b")
        await store.prepared(lease, ())       # else the reaper fails it free, correctly
        rig.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await store.recover()
        assert [(str(event.job_id), event.attempt) for event in produced
                if isinstance(event, IndexEvent)] == [(first.request_id, 1)], produced
        again = await store.claim(first.request_id, "worker-b")
        assert again.generation == claimed.generation + 1
        holds = first.maximum_hold + second.maximum_hold + third.maximum_hold
        balance = rig.extra["balance"](b.ORG_A)
        assert (balance["ledger"], balance["reserved"]) == (Decimal(kit.GRANT), holds), balance
        with bk.connect(database) as conn:       # DRL-R3-2: the holds, not only the summary
            drifts = conn.execute("select ledger_drift, reserved_drift, active_holds from "
                                  "infrx.wallet_reconciliation where org_id = %s",
                                  (b.ORG_A,)).fetchone()
        assert drifts == (0, 0, holds), drifts
        assert bk.drift(database) == [], "the reconcile runbook's detector reports drift"
        # DRL-R4-3: and it does report one - a hold released behind the summary's back (this
        # scratch database is dropped afterwards)
        with bk.connect(database) as conn:
            conn.execute("update infrx.credit_holds set state = 'released' "
                         "where request_id = %s", (first.request_id,))
        assert {str(row[0]) for row in bk.drift(database)} == {b.ORG_A}, \
            "the detector missed a released hold"

    with bk.scratch("infrx_i3b_jobs") as (database,):
        with bk.connect(database) as conn:
            pgstate.apply_migrations(conn)
            pgstate.install_test_clock(conn)
            pgtesting.seed(conn)
            conn.execute(d_migrations.SEED_MARLIN.read_text())
        factory = pgtesting.make_jobstore_factory(lambda: database, bk.conninfo)
        kit.run(lambda: body(database, factory))


def test_i3b_rc04b_settlement_across_a_database_loss(monkeypatch):
    """The other half of rc04 (E3B phase 3: D5's `terminalize` merged): two jobs claimed on
    PgJobStore, then PostgreSQL SIGKILLed and restarted. Job 1's settlement COMMITTED before
    the kill and its answer was lost; job 2 was still running. After the restart, with the
    store object built before the kill: job 1's identical retry replays the committed
    outcome, job 2 settles, each once at its ADMITTED price (one usage projection each), both
    holds are released, and the wallet's summary agrees with the ledger and the holds (zero
    drift; the runbook's detector silent)."""
    import pgstate

    import test_restore as bk
    from infrx.contracts.conformance import builders as b
    from infrx.contracts.fakes.support import CrashAfterCommit
    from infrx.contracts.records import ExecutionMode, OutboxKind, SettlementState
    from infrx.state import migrations as d_migrations
    from infrx.state import pgtesting
    monkeypatch.setenv("PGPASSWORD", bk.pg_password())
    tokens = b.usage(1200, 200)         # inside the 256-token envelope

    async def body(database: str, factory) -> None:
        h = factory()
        h.extra["grant"](b.ORG_A, kit.GRANT)
        store = h.port
        admitted, leases = [], []
        for n in (1, 2):
            request = b.request(h, mode=ExecutionMode.async_, max_output_tokens=256)
            admission = await store.admit(request, b.idem(request, f"rc04b-{n}"))
            lease = await store.claim_preparation(admission.request_id, "prep-a")
            await store.prepared(lease, ())
            admitted.append(admission)
            leases.append(await store.claim(admission.request_id, "worker-a"))
        proposals = [b.outcome(a.request_id, h, tokens=tokens) for a in admitted]
        h.failures.crash_after_commit("complete")
        with pytest.raises(CrashAfterCommit):
            await store.complete(leases[0], proposals[0])
        committed = (await store.get_owned(b.ORG_A, admitted[0].job_handle))[1]
        bk.kill_postgres(database)
        rig = factory()                 # the hooks' and the clock's own connection, anew
        replayed = await store.complete(leases[0], proposals[0])
        settled = await store.complete(leases[1], proposals[1])
        assert replayed == committed, (replayed, committed)
        debits = Decimal(0)
        for admission, outcome in zip(admitted, (replayed, settled)):
            debit = admission.price_snapshot.debit(1200, 200)
            assert (outcome.settlement_state, outcome.debit, outcome.usage) == (
                SettlementState.settled, debit, tokens), outcome
            assert (await store.get_owned(b.ORG_A, admission.job_handle))[1] == outcome
            assert rig.extra["outbox_kinds"](admission.request_id).count(
                OutboxKind.usage_projection) == 1, admission.request_id
            debits += debit
        balance = rig.extra["balance"](b.ORG_A)
        assert (balance["ledger"], balance["reserved"]) == (Decimal(kit.GRANT) - debits, 0), \
            balance
        with bk.connect(database) as conn:
            drifts = conn.execute("select ledger_drift, reserved_drift, active_holds from "
                                  "infrx.wallet_reconciliation where org_id = %s",
                                  (b.ORG_A,)).fetchone()
        assert drifts == (0, 0, 0), drifts
        assert bk.drift(database) == [], "the reconcile runbook's detector reports drift"

    with bk.scratch("infrx_i3b_settle") as (database,):
        with bk.connect(database) as conn:
            pgstate.apply_migrations(conn)
            pgstate.install_test_clock(conn)
            pgtesting.seed(conn)
            conn.execute(d_migrations.SEED_MARLIN.read_text())
        factory = pgtesting.make_jobstore_factory(lambda: database, bk.conninfo)
        kit.run(lambda: body(database, factory))


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
    objects = Outage()
    outage_drill(tmp_path, objects, down=lambda: setattr(objects, "down", True),
                 up=lambda: setattr(objects, "down", False))


def outage_drill(tmp_path, objects, *, down, up) -> None:
    """rc05's drill on any object store with an outage switch (`down`/`up`)."""
    world = kit.World()
    with_media(world, objects, str(tmp_path / "cache"))
    clip = kit.m_support().mp4(seconds=10.0)

    async def body():
        short, long = await video_job(world, 0, clip), await video_job(world, 1, clip)
        before = world.scrape()
        down()                                       # a short outage
        assert not await probe(world, "object_store", lambda: objects.head("probe"))
        assert not await world.prepare(short.request_id, "prep-a")
        assert "ComponentDown" in kit.fired(world, before)
        world.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        await world.reap()
        up()
        assert await probe(world, "object_store", lambda: objects.head("probe"))
        assert await world.prepare(short.request_id, "prep-b")
        await world.dispatch(short.request_id)
        await world.finish(world.loop())
        down()                                       # an outage longer than the budget
        assert not await prepare_until_settled(world, long.request_id)
        up()
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 1,
                                       "failed/preparation_failed": 1}, summary
        assert "ReaperTerminalized" in kit.fired(world, before)
    kit.run(body)


def test_i3b_rc05b_an_object_store_outage_on_minio_through_the_s3_adapter(tmp_path,
                                                                            monkeypatch):
    """rc05's drill against E2's MinIO through M1-L2's `S3ObjectStore` (E3B phase 3: the
    adapter merged, so this runs), the outage a real network partition: MinIO leaves the
    stack's network (`harness.disconnect_container`) and rejoins it. The store's own answer
    during the partition is `dependency_unavailable`, so the probe reads it down, the short
    outage is retried and completes, the long one is released `preparation_failed`, free.
    MinIO's local literals are botocore's only credentials; the objects live under a prefix
    of this run's own in E2's bucket, removed afterwards."""
    import uuid

    from infrx.media.s3 import S3ObjectStore
    kit.needs_stack()
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    for name, value in stack.s3_env().items():
        monkeypatch.setenv(name, value)
    client = harness.s3_client()
    with contextlib.suppress(Exception):                     # already there
        client.create_bucket(Bucket=harness.S3_BUCKET)
    prefix = f"{harness.OBJECT_PREFIX}rc05b-{uuid.uuid4().hex}/"
    objects = S3ObjectStore.connect(harness.S3_BUCKET, prefix, harness.s3_endpoint())
    partition = []
    try:
        outage_drill(tmp_path, objects,
                     down=lambda: partition.append(harness.disconnect_container("s3")),
                     up=lambda: partition.pop().revert())
    finally:
        while partition:
            partition.pop().revert()
        listed = client.list_objects_v2(Bucket=harness.S3_BUCKET, Prefix=prefix)
        for item in listed.get("Contents", []):
            client.delete_object(Bucket=harness.S3_BUCKET, Key=item["Key"])


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
    `TimeoutStopSec`: W3's WorkerService and I2B's unit exist; the composition root the
    unit starts (`python -m infrx.worker`, I2B request 4) does not. Fails the day
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
    kit.pending("I2B-R4", why="no worker process entry point (python -m infrx.worker) to "
                             "SIGTERM")


def _outcome(call) -> str:
    """What a pending drill did, as text: its skip reason, its failure, or its refusal."""
    try:
        call()
    except pytest.skip.Exception as skipped:
        return str(skipped)
    except pytest.fail.Exception as failed:
        return f"failed: {failed}"
    except AssertionError as refused:
        return f"refused: {refused}"
    return "returned"


def test_i3b_rc00_each_pending_drill_names_its_pinned_owner_and_an_unknown_id_is_refused(
        monkeypatch):
    """DR-4: a pending count is only honest if the id is. `kit.pending` refuses an id outside
    the vocabulary (and none at all); each pending drill pends on exactly the id pinned here,
    so a swapped or misspelt owner fails instead of being counted. Layer 0. (rc03 runs since
    the cutover mounted the ingress, and rc04b since D5: E3B phase 3.)"""
    assert _outcome(lambda: kit.pending("NOPE", why="x")).startswith("refused: "), "NOPE"
    assert _outcome(lambda: kit.pending(why="x")).startswith("refused: "), "no id"
    pinned = {test_i3b_rc08b_a_sigterm_drain_of_the_worker_process_is_pending_on_w3: "I2B-R4"}
    for case, owner in pinned.items():
        assert _outcome(case).startswith(f"PENDING[{owner}] "), (case.__name__, _outcome(case))


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

# The copy's, under the mutation runner: i3bm94/i3bm97/i3bm98 mutate rollback.sh and lib.sh.
DEPLOY = kit.ROOT / "apps" / "infrx-api" / "deploy"
ENV_FILE = "etc/marlin2b-gateway.env"
UNITS = ("marlin2b-vllm.service", "marlin2b-gateway.service", "infrx-worker.service",
         "infrx-valkey.service")
# What install.sh step 3 backs up (and rollback.sh restores): the env file, the edge, the units.
BACKED_UP = (ENV_FILE, "etc/caddy/Caddyfile", "etc/caddy/infrx/Caddyfile",
             "etc/caddy/infrx/Caddyfile.maintenance", *(f"etc/systemd/system/{u}" for u in UNITS))
# The host's binaries rollback.sh calls, stubbed: each records its argv and succeeds, except
# a call naming $INFRX_I3B_FAIL, which fails - always (rc10b: a readiness probe that never
# answers), or only its first $INFRX_I3B_FAILS times (rc10c: one that answers late).
STUB = ('#!/usr/bin/env bash\necho "$(basename "$0") $*" >> "$INFRX_I3B_EVENTS"\n'
        'case "$*" in *"${INFRX_I3B_FAIL:-<none>}"*)\n'
        '  [ "$(grep -cF -- "$*" "$INFRX_I3B_EVENTS")" -gt "${INFRX_I3B_FAILS:-999999}" ] '
        '|| exit 1 ;;\nesac\n')
IMAGE = {"previous": "sha256:" + "a" * 64, "current": "sha256:" + "b" * 64}


def release(image: str | None) -> dict[str, str]:
    """A host's backed-up files at one release: the pilot env file pinning `image` (None: the
    pre-I2B monolith's, no mode) and I2B's units and edge, EACH marked with the release, so a
    rollback that leaves any one of them (the edge included: i3bm94) in place is caught."""
    files = {ENV_FILE: f"INFRX_MODE=pilot\nINFRX_IMAGE={image}\n" if image
             else "MODEL_ID=nemostation/marlin-2b\n"}
    for unit in UNITS:
        files[f"etc/systemd/system/{unit}"] = (DEPLOY / unit).read_text() + f"# {image}\n"
    for site in ("Caddyfile", "Caddyfile.maintenance"):
        files[f"etc/caddy/infrx/{site}"] = (DEPLOY / site).read_text() + f"# {image}\n"
    files["etc/caddy/Caddyfile"] = files["etc/caddy/infrx/Caddyfile"]
    return files


def install(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text)


def backup(root: Path, into: Path) -> Path:
    """install.sh step 3, as it runs it: the present files in files.tar, the rest in absent."""
    into.mkdir(parents=True)
    present = [path for path in BACKED_UP if (root / path).exists()]
    subprocess.run(["tar", "-C", str(root), "-cpf", str(into / "files.tar"), *present],
                   check=True)
    (into / "absent").write_text("".join(f"{path}\n" for path in BACKED_UP
                                         if path not in present))
    return into


def on_host(root: Path) -> dict[str, str]:
    return {path: (root / path).read_text() for path in BACKED_UP if (root / path).exists()}


def rollback_sh(tmp_path: Path, root: Path, target: Path, fail: str = "",
                fails: int | None = None,
                ready_s: int = 1) -> tuple[subprocess.CompletedProcess, list[str]]:
    """I2B's `deploy/rollback.sh <backup>`, unmodified, against the sandbox root, with the
    stubs first on PATH (a call naming `fail` exits 1: always, or its first `fails` times).
    Returns its result and the commands it issued, in order."""
    stubs, events = tmp_path / "stub-bin", tmp_path / "events.log"
    if not stubs.exists():
        stubs.mkdir()
        for name in ("systemctl", "docker", "curl"):
            (stubs / name).write_text(STUB)
            (stubs / name).chmod(0o755)
    events.unlink(missing_ok=True)
    done = subprocess.run(["bash", str(DEPLOY / "rollback.sh"), str(target)],
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, "INFRX_ROOT": str(root),
                               "PATH": f"{stubs}:{os.environ['PATH']}",
                               "INFRX_I3B_EVENTS": str(events), "INFRX_I3B_FAIL": fail,
                               "INFRX_I3B_FAILS": "" if fails is None else str(fails),
                               "POLL_S": "0.01",
                               "READY_S": str(ready_s)})
    return done, (events.read_text().splitlines() if events.exists() else [])


# The commands rollback.sh issues for a pilot -> pilot rollback, in order: stop the runtime
# (the worker drains), restore the files (tar, real), reload the units, restart the restored
# runtime, wait for BOTH readiness probes, and only then reload the edge.
ROLLBACK_COMMANDS = [
    "systemctl stop infrx-worker marlin2b-gateway",
    "systemctl daemon-reload",
    "systemctl restart infrx-worker marlin2b-gateway",
    "curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8001/readyz",
    "curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8002/readyz",
    "docker inspect caddy",
    "docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile "
    "--address unix//config/admin.sock",
]


def test_i3b_rc10_a_rollout_rollback_loses_no_job_and_restores_the_previous_runtime(
        tmp_path, monkeypatch):
    """OPS-RECOVER (rollout rollback), rollback.md's procedure end to end. A host serves
    pilot at the current release with accepted work (one done, one mid-attempt, two queued);
    the previous release is a pilot (metered) runtime, backed up by install.sh step 3.

    2. maintenance: bk04's statement, as service_role, on PostgreSQL - admission refuses
       with 55000; 3. drain and fence: W2's drain releases the attempt, settles nothing;
    4. I2B's rollback.sh: to the pre-pilot backup it is REFUSED (exit 2, nothing run or
       written: a pilot host is never handed to an unmetered runtime); to the previous
       release it issues exactly `ROLLBACK_COMMANDS` and the env file (the image pin) and
       every unit and edge file are the previous release's, byte for byte;
    5. the restored runtime's reaper requeues the released job after the lease TTL and the
       index is rebuilt from the durable snapshot; 6. maintenance off: admission accepted;
    7. the restored worker finishes everything and `reconcile()` holds: no accepted job
       lost, one settlement each.

    Stubbed: systemctl, docker and curl (argv recorded, exit 0) and the host root (a
    sandbox, `INFRX_ROOT`). The worker's stop is W2's drain run in process, and the
    restored runtime is a new WorkerLoop started after rollback.sh restarted its unit."""
    import psycopg

    import test_restore as bk
    monkeypatch.setenv("PGPASSWORD", bk.pg_password())
    world = kit.World()
    root = tmp_path / "root"
    # the premise of "byte for byte": no backed-up file is the same in both releases (DR-1)
    assert [path for path in BACKED_UP
            if release(IMAGE["previous"])[path] == release(IMAGE["current"])[path]] == []
    install(root, release(IMAGE["previous"]))
    previous = backup(root, tmp_path / "backups" / "previous")
    install(root, release(IMAGE["current"]))              # the rollout being rolled back
    install(tmp_path / "monolith", release(None))
    monolith = backup(tmp_path / "monolith", tmp_path / "backups" / "monolith")
    admission = "select infrx.require_feature('credit_admission')"

    async def body(conn):
        jobs = [await world.queued(n % 2) for n in range(4)]
        await world.dispatch(*(job.request_id for job in jobs))
        await world.loop(worker_id="worker-current").run(concurrency=1, stop_when_idle=True,
                                                         max_claims=1)
        engine = Hanging(world.clock, publish=False)
        current = world.loop(engine, "worker-current")
        running = asyncio.create_task(current.run(concurrency=1, stop_when_idle=False))
        await until(lambda: len(engine.running) == 1)

        with conn.transaction():                                            # 2. maintenance
            bk.as_role(conn, "service_role", bk.MAINTENANCE, (False,))
        with pytest.raises(psycopg.Error) as refused:
            with conn.transaction():
                bk.as_role(conn, "service_role", admission)
        assert refused.value.sqlstate == "55000", refused.value

        report = await asyncio.wait_for(current.drain(within_s=0.05), 5)    # 3. drain, fence
        await asyncio.wait_for(running, 5)
        assert (report.released, world.jobs.jobs[engine.running[0]].outcome) == (1, None)

        written = on_host(root)                                             # 4. rollback.sh
        done, issued = rollback_sh(tmp_path, root, monolith)
        assert done.returncode == 2 and "drain.sh pause" in done.stderr, done.stderr
        assert (issued, on_host(root)) == ([], written), "a refused rollback changed the host"
        done, issued = rollback_sh(tmp_path, root, previous)
        assert done.returncode == 0, done.stderr
        assert issued == ROLLBACK_COMMANDS, issued
        assert on_host(root) == release(IMAGE["previous"])

        world.clock.advance(DEFAULTS.lease_ttl_s + 1)                       # 5. reap, rebuild
        assert len(await world.reap()) == 1
        assert await world.scheduler.rebuild(world.snapshot()) == 3
        with conn.transaction():                                            # 6. resume
            bk.as_role(conn, "service_role", bk.MAINTENANCE, (True,))
        with conn.transaction():
            bk.as_role(conn, "service_role", admission)
        await world.finish(world.loop(worker_id="worker-restored"))         # 7. reconcile
        summary = await world.reconcile()
        assert summary["terminal"] == {"succeeded/completed": 4}, summary

    with bk.scratch("infrx_i3b_rollback") as (database,):
        bk.apply(database, bk.migrations(1, 9999))
        with bk.connect(database) as conn:
            kit.run(lambda: body(conn))


@pytest.mark.parametrize("probe", ("8001", "8002"))
def test_i3b_rc10b_a_rollback_whose_restored_runtime_is_not_ready_never_reloads_the_edge(
        tmp_path, probe):
    """rollback.md step 4, exit 4 (DR-3): the restored runtime restarts but one readiness
    probe never answers - the gateway's (8001) or the worker's (8002). rollback.sh polls it
    for READY_S (3 s here: at least 2 s of wall time, bash's SECONDS being whole seconds),
    then gives up with exit 4 and never touches the edge (no `docker` call, so the running
    Caddy keeps serving what it served and the operator stays in maintenance); the probe
    after a failed one is never reached. The files are already the previous release's: the
    restore precedes the probe. Stubs and sandbox as rc10; no database."""
    root = tmp_path / "root"
    install(root, release(IMAGE["previous"]))
    previous = backup(root, tmp_path / "backups" / "previous")
    install(root, release(IMAGE["current"]))
    ready = f"http://127.0.0.1:{probe}/readyz"
    started = time.monotonic()
    done, issued = rollback_sh(tmp_path, root, previous, fail=ready, ready_s=3)
    waited = time.monotonic() - started
    assert done.returncode == 4 and "the restored runtime is not ready" in done.stderr, \
        (done.returncode, done.stderr)
    head = ROLLBACK_COMMANDS[:3 + (probe == "8002")]      # stop, reload, restart (, 8001 ok)
    assert issued[:len(head)] == head, issued
    retries = issued[len(head):]
    assert set(retries) == {f"curl -fsS -o /dev/null --max-time 5 {ready}"}, issued
    # DRL-1/R4-2: retried every POLL_S (0.01 s: ~150-200 probes in 3 s), not once or twice,
    # not every READY_S/2 and not in a busy loop
    assert 10 <= len(retries) < 400, f"{len(retries)} probes in {waited:.2f} s"
    # DRL-R3-1/R4-1: for READY_S (between 2 and 4 s here), not a fixed budget of 1 s or 5 s+
    assert 2 <= waited < 4, f"gave up after {waited:.2f} s of a 3 s READY_S ({len(retries)} probes)"
    assert on_host(root) == release(IMAGE["previous"])


def test_i3b_rc10c_a_gateway_that_answers_late_is_waited_for_and_the_edge_reloaded(tmp_path):
    """rollback.md step 4 (DRL-1): the restored gateway's /readyz fails once and then
    answers - a real gateway is rarely ready at its first probe. rollback.sh probes it again,
    then the worker's, then reloads the edge: exit 0 and exactly rc10's commands with the
    8001 probe issued twice. Stubs and sandbox as rc10; no database."""
    root = tmp_path / "root"
    install(root, release(IMAGE["previous"]))
    previous = backup(root, tmp_path / "backups" / "previous")
    install(root, release(IMAGE["current"]))
    done, issued = rollback_sh(tmp_path, root, previous,
                               fail="http://127.0.0.1:8001/readyz", fails=1)
    assert done.returncode == 0, (done.returncode, done.stderr)
    assert issued == ROLLBACK_COMMANDS[:4] + ROLLBACK_COMMANDS[3:], issued
    assert on_host(root) == release(IMAGE["previous"])
