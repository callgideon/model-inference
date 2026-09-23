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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.state import FakeStreamStore
from infrx.contracts.records import ExecutionMode, IndexEvent, JobState, OutboxKind
from infrx.gateway import pilot
from infrx.gateway.routes import ingress
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


@pytest.mark.parametrize("missing", ["catalog", "stream", "objects"])
def test_f_base__a_pilot_is_not_built_without_its_durable_adapters(missing):
    """D5's catalog, D4's journal and an object store that outlives the process have no
    real adapter yet: the composition refuses, naming the collaborator (never a value)."""
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
            while not outbox.reads:
                await asyncio.sleep(0)
            for _ in range(20):
                await asyncio.sleep(0)
            assert event.event_id in await index.members()
        assert all(task.done() for task in rt.lifetime.tasks)

    rs.run(body())


def test_f_base__exactly_one_chat_route_and_it_is_the_ingress():
    """G1R review C4: the composition root asserts what the route table serves, not only
    which modules `ROUTERS` names - one handler at `/v1/chat/completions`, the ingress's."""
    app, _ = support.cutover_app()
    (route,) = [r for r in app.routes if getattr(r, "path", "") == support.CHAT_PATH]
    assert route.endpoint.__module__ == ingress.__name__
    ingress.assert_route_table(app)
    legacy = support.legacy_app()
    legacy.state.runtime.mode = "dev"
    ingress.register(legacy, legacy.state.runtime, support.deps())
    second, _ = support.cutover_app()

    @second.post(support.CHAT_PATH)
    async def shadow():
        return {}

    for app in (legacy, second, FastAPI()):
        with pytest.raises(RuntimeMisconfigured):
            ingress.assert_route_table(app)
