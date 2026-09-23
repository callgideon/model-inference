#!/usr/bin/env python3
"""F-BASE / M-FAILCLOSED (G2 item 5): the pilot composition `build_ingress_deps`.

    uv run --frozen pytest -q tests/g/test_composition.py

What the coordinator's `create_app` will call at the cutover, exercised with the contract
fakes standing in for the three adapters that do not exist yet (D5's catalog, D4's journal,
M's durable object store) and for PostgreSQL and Valkey. The real `PgJobStore` pool and the
Valkey index are the defaults; they are constructed here only as far as needs no server.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.state import FakeStreamStore
from infrx.contracts.records import ExecutionMode, IndexEvent, JobState, OutboxKind
from infrx.gateway import pilot
from infrx.gateway.routes import chat, ingress
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler

from . import relay_support as rs, support


class Journal(FakeStreamStore):
    """The contract journal plus D4's `usage()` (the readiness probe's body)."""

    down = False

    async def usage(self):
        if self.down:
            raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")
        return {"reserved_bytes": 0, "stored_bytes": 0, "charged_bytes": 0, "chunks": 0}


class Outbox:
    """D2's dispatch outbox, reduced to what Q3's relay reads - one pending row."""

    def __init__(self, event) -> None:
        self.pending, self.reads = [event], 0

    async def dispatch_pending(self, *, limit, worker_id, redelivery_s):
        self.reads += 1
        events, self.pending = tuple(self.pending), []
        return events

    async def acknowledge_dispatch(self, event_ids, *, worker_id):
        return len(event_ids)

    async def release_dispatch(self, event_ids):
        return len(event_ids)

    async def dispatch_snapshot(self):
        return ()


def composed(regime="legacy_usd", *, card="", **kw):
    """(rt, deps) as the cutover composes them, over the fakes, on one fake clock."""
    harness = credit_jobstore_factory()
    jobs, clock = harness.port, harness.clock
    jobs.catalog = catalog = support.catalog()
    config = support.settings()
    config.deployment = config.deployment.replace(accounting_regime=regime)
    config.pilot = config.pilot.replace(active_rate_card_version=card)
    rt = support.runtime(config, sb=support.supabase(rows=(rs.CONSUMER_ROW,)),
                         clock=lambda: clock.now().timestamp())
    fields = dict(catalog=catalog, stream=Journal(jobs), objects=InMemoryObjectStore(),
                  jobs=jobs, index=MemoryScheduler(clock.now))
    fields.update(kw)
    return rt, pilot.build_ingress_deps(rt, **fields)


def served(rt, deps):
    app = FastAPI()
    app.state.runtime = rt
    rt.app = app
    ingress.register(app, rt, deps)
    return app


@pytest.mark.parametrize("missing", ["catalog", "stream", "objects", "jobs"])
def test_f_base__a_pilot_is_not_built_without_its_durable_adapters(missing):
    """`build_ingress_deps` builds nothing of its own (`adapters_from_env` does, for
    `create_app`): without a catalog, a journal, an object store that outlives the process
    or a job store it refuses, naming the collaborator (never a value)."""
    with pytest.raises(RuntimeMisconfigured) as refused:
        composed(**{missing: None})
    assert missing in str(refused.value)


@pytest.mark.parametrize("regime", ["legacy_usd", "credit"])
def test_f_base__the_composed_pilot_admits_in_the_configured_regime(regime):
    """Wire-in request "G1R composition": `ACCOUNTING_REGIME` picks the admission, and a
    CREDIT admission is held to `ACTIVE_RATE_CARD_VERSION`. One request through the composed
    ingress; the store says which regime took it."""
    card = support.catalog().rate_cards[support.IDS.prod_deployment].rate_card_version
    rt, deps = composed(regime, card=card if regime == "credit" else "")
    jobs = rt.relay.jobs
    if regime == "legacy_usd":
        jobs.grant(rs.CONSUMER_ROW["org_id"], "100")
    rt.relay.sleep = lambda _s: asyncio.sleep(0, jobs.clock.advance(3_600))
    reply = rs.run(rs.call(served(rt, deps), rs.body()))
    assert len(jobs.jobs) == 1, reply.body
    (job,) = jobs.jobs.values()
    assert (job.credit is not None) == (regime == "credit"), reply.body
    assert job.state is JobState.cancelled and reply.status == 504      # the wait's bound
    if regime == "credit":
        assert job.credit.pins.rate_card_version == card


def test_f_base__one_large_body_bound_and_one_media_store_per_process():
    """F2R-A IR-A5 / M2 request 5 / G4U request (a): the ingress and the upload router share
    one `LargeBodies` (sized by `LARGE_BODY_*`) and one media store - the instance the relay
    stages from is the one uploads finalize into."""
    rt, deps = composed()
    assert deps.large_bodies is rt.large_bodies
    deployment = rt.settings.deployment
    assert (rt.large_bodies.limit, rt.large_bodies.threshold) == (
        deployment.large_body_limit, deployment.large_body_threshold_bytes)
    assert getattr(rt, "media_store", None) is rt.relay.media
    assert deps.accept == rt.relay.accept and deps.catalog is rt.relay.catalog


def test_f_base__readiness_probes_are_cached_answers_the_lifetime_refreshes():
    """`REQUIRED_CHECKS` are sync callables read by the async `/readyz`: each is a cached
    answer, asked once on its own thread at registration (inside a running loop, as under
    `uvicorn --factory`) and refreshed by the lifetime task; a failing check is `False`
    with its text in the log only."""
    rt, deps = composed()
    stream = rt.relay.stream

    async def body():
        # First answers at registration, inside this running loop: pilot starts.
        app = served(rt, deps)
        assert ingress.component_state(deps.checks) == {"price_source": "ok", "journal": "ok"}
        stream.down = True
        assert deps.checks["journal"]() is True                 # cached: nothing blocks
        for probe in rt.lifetime.probes:
            await probe.refresh()
        assert ingress.component_state(deps.checks) == {"price_source": "ok",
                                                        "journal": "unavailable"}
        return app

    app = rs.run(body())
    response = TestClient(app, client=("127.0.0.1", 1)).get(support.READY_PATH)
    assert response.status_code == 503 and "secret" not in response.text


def test_f_base__the_pool_sets_the_service_role_on_every_connection():
    """D2 request 7: 0004's BYPASSRLS is not inherited, so every pooled connection runs `set
    role service_role` (and the deployment's statement timeout) before it is handed out."""
    executed = []

    class Connection:
        async def execute(self, sql, *args):
            executed.append(sql)

    rs.run(pilot.configure_connection(15_000)(Connection()))
    assert executed == ["set role service_role", "set statement_timeout = 15000"]
    pool, _connect = pilot.connection_pool(support.settings())
    deployment = support.settings().deployment
    assert (pool.min_size, pool.max_size) == (deployment.database_pool_min_size,
                                              deployment.database_pool_max_size)
    # Review C1: the pool that is built carries that hook - driven as psycopg drives it.
    executed.clear()
    assert pool._configure is not None, "the built pool has no configure hook"
    rs.run(pool._configure(Connection()))
    assert executed == ["set role service_role", "set statement_timeout = "
                        f"{deployment.database_pool_statement_timeout_ms}"]


def test_f_base__the_lifespan_runs_the_dispatch_relay_until_shutdown():
    """Q3 request 2: the reconciler feeds the index from the outbox for the process lifetime
    and stops on shutdown; a unique worker id per process."""
    harness = credit_jobstore_factory()
    event = IndexEvent(event_id=harness.ids.event_id(), job_id=harness.ids.uuid(),
                       org_id=support.ORG, key_id=support.KEY,
                       kind=OutboxKind.inference_dispatch, execution_mode=ExecutionMode.sync,
                       available_at=harness.clock.now())
    outbox = Outbox(event)
    index = MemoryScheduler(harness.clock.now)
    rt, deps = composed(jobs=outbox, index=index)
    other_rt, _ = composed(jobs=outbox, index=index)
    assert rt.lifetime.reconciler.worker_id != other_rt.lifetime.reconciler.worker_id
    app = served(rt, deps)

    async def body():
        async with pilot.lifespan(app):
            for _ in range(200):                  # bounded: a relay that never runs fails
                if event.event_id in await index.members():
                    break
                await asyncio.sleep(0)
            assert outbox.reads and event.event_id in await index.members()
        assert all(task.done() for task in rt.lifetime.tasks)

    rs.run(body())


def test_f_base__exactly_one_chat_route_and_it_is_the_ingress():
    """G1R review C4: the composition root asserts what the route table serves, not only
    which modules `ROUTERS` names - one handler at `/v1/chat/completions`, the ingress's."""
    app, _ = support.cutover_app()
    (route,) = [r for r in app.routes if getattr(r, "path", "") == support.CHAT_PATH]
    assert route.endpoint.__module__ == ingress.__name__
    ingress.assert_route_table(app)
    legacy = FastAPI()
    legacy.state.runtime = rt = support.runtime(support.settings("dev"))
    chat.register(legacy, rt)                     # the retired F1 route, then the ingress
    ingress.register(legacy, rt, support.deps())
    second, _ = support.cutover_app()

    @second.post(support.CHAT_PATH)
    async def shadow():
        return {}

    # Review C6: a pattern route registered earlier serves the path without being "at"
    # it; Starlette picks it first, so the table is refused.
    shadowed = FastAPI()
    shadowed.state.runtime = rt = support.runtime(support.settings("dev"))

    @shadowed.post("/v1/{rest:path}")
    async def pattern(rest: str):
        return {"served_by": "shadow"}

    ingress.register(shadowed, rt, support.deps())
    for app in (legacy, second, shadowed, FastAPI()):
        with pytest.raises(RuntimeMisconfigured):
            ingress.assert_route_table(app)


# === G2 review round 2 =====================================================================
def test_f_base__the_credit_price_probe_requires_the_active_card():
    """Review honesty-H-B4 / C4: in the CREDIT regime the served model's approved card must
    be the one this deployment was approved to serve (`ACTIVE_RATE_CARD_VERSION`)."""
    card = support.catalog().rate_cards[support.IDS.prod_deployment].rate_card_version
    _, active = composed("credit", card=card)
    _, other = composed("credit", card="rc_not_this_deployments")
    assert active.checks["price_source"]() is True
    assert other.checks["price_source"]() is False


@pytest.mark.parametrize("fault", ["hangs", "raises"])
def test_f_base__a_probe_that_hangs_or_fails_reads_unavailable_and_leaves_no_thread(fault):
    """Review H-B4 / C3 (M-FAILCLOSED): a first answer that hangs past the bound or raises
    reads False, and the thread it was asked on has ended - a refusal to start can finish."""
    async def check():
        if fault == "raises":
            raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")
        await asyncio.Event().wait()

    before = set(threading.enumerate())
    assert pilot.Probe(check, timeout_s=0.05)() is False
    assert set(threading.enumerate()) <= before


def test_f_base__the_refresh_loop_replaces_a_cached_answer_when_a_check_hangs(monkeypatch):
    """Review C2: the lifetime task keeps asking. A check that answers, then hangs, reads
    unavailable within its bound - a hung dependency never keeps a cached True."""
    monkeypatch.setattr(pilot, "PROBE_EVERY_S", 0.01)
    answers = []

    async def check():
        answers.append(True)
        if len(answers) > 2:                  # healthy at registration and once more
            await asyncio.Event().wait()
        return True

    probe = pilot.Probe(check, timeout_s=0.05)

    async def body():
        assert probe() is True
        stop = asyncio.Event()
        task = asyncio.create_task(pilot._refresh((probe,), stop))
        for _ in range(300):                  # bounded: a loop that stops asking fails
            if probe.value is False:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 1)
        return probe.value

    assert rs.run(body()) is False and len(answers) >= 3


def test_f_base__the_lifespan_opens_the_pool_first_and_closes_it_last():
    """Review C5: the pool is opened before the lifetime tasks start and closed after they
    have finished - and after the relay's cancels drained (review r2 stream-C2-1)."""
    rt, deps = composed()
    order = []
    drain = rt.relay.drain

    async def drained(timeout_s):
        order.append(("drain",))
        await drain(timeout_s)

    rt.relay.drain = drained

    class Pool:
        async def open(self):
            order.append(("open", len(rt.lifetime.tasks)))

        async def close(self):
            order.append(("close", all(task.done() for task in rt.lifetime.tasks)))

    rt.lifetime.pool = Pool()
    app = served(rt, deps)

    async def body():
        async with pilot.lifespan(app):
            order.append(("serving", len(rt.lifetime.tasks)))

    rs.run(body())
    assert order == [("open", 0), ("serving", 2), ("drain",), ("close", True)]


def test_f_base__shutdown_drains_the_relays_durable_cancels():
    """Review stream-S1, r2 stream-C2-1/C2-3. The process stops while two sync waits'
    durable cancels are still in flight (a slow store), and the handlers are not waited
    for. The lifespan's shutdown drains every one of them before the pool closes - a
    cancel sent after the close would fail - so both jobs are cancelled rather than left
    to their stored deadline."""
    rt, deps = composed()
    jobs = rt.relay.jobs
    jobs.grant(rs.CONSUMER_ROW["org_id"], "100")
    cancel, waiting, in_flight, closed = jobs.cancel, [], [], []
    delays = [0.1, 0.3]

    class Pool:
        async def open(self):
            pass

        async def close(self):
            closed.append(True)

    async def slow_cancel(org_id, handle, **cause):
        in_flight.append(handle)
        await asyncio.sleep(delays.pop(0))    # a store round trip still in flight
        if closed:
            raise ConnectionError("the connection pool is closed")
        return await cancel(org_id, handle, **cause)

    async def nap(_seconds):
        waiting.append(True)
        await asyncio.Event().wait()          # the jobs run on; the waits are still waiting

    async def until(done):
        for _ in range(500):                  # bounded: a step that never happens fails
            if done():
                return
            await asyncio.sleep(0)
        pytest.fail("the handlers never reached that step")

    jobs.cancel, rt.relay.sleep, rt.lifetime.pool = slow_cancel, nap, Pool()
    app = served(rt, deps)

    async def body():
        async with pilot.lifespan(app):
            handlers = [asyncio.ensure_future(rs.call(app, rs.body())) for _ in range(2)]
            await until(lambda: len(waiting) == 2)
            for handler in handlers:
                handler.cancel()              # the process stops ...
            await until(lambda: len(in_flight) == 2)
            for handler in handlers:
                handler.cancel()              # ... and does not wait for the handlers
            await asyncio.gather(*handlers, return_exceptions=True)

    rs.run(body())
    states = [job.state for job in jobs.jobs.values()]
    assert states == [JobState.cancelled, JobState.cancelled], states


# === the cutover: `create_app` composes from settings (G2 R2-1) ===========================
class Connection:
    """A psycopg connection as far as the stores and the configure hook drive it."""

    def __init__(self) -> None:
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def close(self):
        pass


def cutover_app(config, **adapters):
    from infrx.gateway.app import create_app
    return create_app(config, client=support.upstream(), sb=support.supabase(), **adapters)


@pytest.mark.parametrize("mode", ["pilot", "dev", "test"])
@pytest.mark.parametrize("bucket", ["", "infrx-media-bucket"])
def test_f_base__create_app_never_stages_into_process_memory(mode, bucket):
    """With no object store injected, `create_app` refuses to start in every mode, naming
    `S3_MEDIA_BUCKET` and never its value: unset, nothing is configured; set, there is no S3
    adapter yet (M1 limit 2). It never falls back to the in-memory store."""
    config = support.settings(mode, s3_media_bucket=bucket)
    for injected in ({}, {"catalog": support.catalog()}):
        with pytest.raises(RuntimeMisconfigured) as refused:
            cutover_app(config, **injected)
        assert "S3_MEDIA_BUCKET" in str(refused.value)
        assert "infrx-media-bucket" not in str(refused.value)


def test_f_base__create_app_builds_the_stores_it_is_not_given_on_one_pool(monkeypatch):
    """Each adapter not injected comes from settings: D5's catalog, D4's journal and the job
    store on ONE pool that `lifespan` opens (not yet open: nothing connects to build them),
    while an injected one is used as given. The database here is unreachable, so `dev` starts
    and says so."""
    import psycopg

    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.jobstore import PgJobStore
    from infrx.state.journal import PgStreamStore

    async def unreachable(*args, **kw):
        raise psycopg.OperationalError("postgresql://infrx:secret@db/infrx is unreachable")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", unreachable)
    objects, index = InMemoryObjectStore(), MemoryScheduler(lambda: None)
    app = cutover_app(support.settings("dev"), objects=objects, index=index)
    rt = app.state.runtime
    relay, pool = rt.relay, rt.lifetime.pool
    assert isinstance(relay.catalog, PgCatalogDirectory) and relay.catalog is rt.ingress.catalog
    assert isinstance(relay.stream, PgStreamStore) and isinstance(relay.jobs, PgJobStore)
    assert pool is not None and pool.closed
    connects = {relay.catalog._db._connect, relay.jobs._connect, relay.stream._db._connect}
    assert len(connects) == 1, "the three stores do not share one pool"
    assert rt.media_store.objects is objects and rt.lifetime.reconciler.index is index
    assert ingress.component_state(rt.ingress.checks) == {"price_source": "unavailable",
                                                          "journal": "unavailable"}
    # a store built from settings needs a database named: an empty DSN is libpq's defaults
    with pytest.raises(RuntimeMisconfigured, match="requires DATABASE_URL"):
        cutover_app(support.settings("dev", database_url=""), objects=objects, index=index)
    # and a given store is used as given, with no pool of ours
    rt, _ = composed()
    given = cutover_app(support.settings("dev"), catalog=rt.relay.catalog,
                        stream=rt.relay.stream, objects=objects, jobs=rt.relay.jobs,
                        index=index).state.runtime
    assert given.relay.jobs is rt.relay.jobs and given.lifetime.pool is None


def test_f_base__the_startup_probe_connects_on_its_own_until_the_lifespan_opens_the_pool(
        monkeypatch):
    """`Probe`'s first answer is asked inside `create_app`, on a thread and loop of its own,
    before `lifespan` opens the pool (a pool belongs to the loop that opens it). Until then the
    stores' connect opens a connection of its own, configured as a pooled one is (the
    service role, the statement timeout); once the pool is open, it lends a pooled one."""
    import psycopg

    direct, pooled = Connection(), Connection()

    async def connect(dsn, **kw):
        assert kw == {"autocommit": True}
        return direct

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    config = support.settings()
    pool, store_connect = pilot.connection_pool(config)
    assert pool.closed and rs.run(store_connect()) is direct
    assert direct.executed == ["set role service_role", "set statement_timeout = "
                               f"{config.deployment.database_pool_statement_timeout_ms}"]

    async def lend():
        return pooled

    pool._closed, pool.getconn = False, lend       # as `pool.open()` leaves it
    lent = rs.run(store_connect())
    assert lent is not pooled and rs.run(lent.execute("select 1")) is None
    assert pooled.executed == ["select 1"] and direct.executed[2:] == []
