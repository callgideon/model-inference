#!/usr/bin/env python3
"""The cutover's `create_app` from settings against a REAL PostgreSQL: the adapters it
builds from `DATABASE_URL` (`pilot.adapters_from_env`: D5's `PgCatalogDirectory`, D4's
`PgStreamStore`, `PgJobStore`, one pool) answer the pilot's startup probes before the pool
is open (a connection of their own, from `Probe`'s own thread and loop), then through the
pool once `lifespan` opens it. The database is the operator's Marlin catalog seed (the v2
fixtures, `test_catalog_pg`'s template). Only the object store (M1-L2's `S3ObjectStore`
needs an S3-compatible endpoint, which this harness does not run) and the scheduling index
are injected.

    INFRX_D_TASK=d3 uv run --frozen pytest -q tests/d/test_composition_pg.py
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured, Settings
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.v2 import fixtures as v2fix
from infrx.gateway.app import create_app
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import PgJobStore
from infrx.state.journal import PgStreamStore

from . import pgharness
from .checks_reads import seed_hosted_usd
from .test_catalog_pg import fresh

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
CARD = v2fix.BUILDERS["rate_card_marlin.json"]().rate_card_version


def _app(regime: str, card: str, database: str):
    settings = Settings(supabase_url="https://fake.supabase.invalid", supabase_key="service-role",
                        pilot=DEFAULTS.replace(infrx_mode="pilot",
                                               database_url=pgharness.dsn(database),
                                               active_rate_card_version=card))
    settings.deployment = settings.deployment.replace(
        accounting_regime=regime, infrx_release_sha="c0ffee" + "0" * 34,
        infrx_image="sha256:" + "b" * 64)
    return create_app(settings, client=object(), sb=object(), objects=InMemoryObjectStore(),
                      index=MemoryScheduler(lambda: None))


@pytest.mark.parametrize("regime", ["legacy_usd", "credit"])
def test_f_base__create_app_composes_the_pilot_from_settings_on_postgresql(regime):
    database = fresh()
    if regime == "legacy_usd":
        # G7 WR-2: the legacy probe prices the served revision (`usd_price`, P-22 resolved).
        # The catalog template carries no USD row; the hosted project carries two (W7c/W7e).
        # Without them the pilot refuses to start (`price_source`), as it must.
        with pytest.raises(RuntimeMisconfigured, match="price_source"):
            _app(regime, CARD, database)
        with pgharness.connect(database) as conn:
            seed_hosted_usd(conn)
    if regime == "credit":
        # the CREDIT pin is the database's active card, or the pilot does not start
        with pytest.raises(RuntimeMisconfigured, match="price_source"):
            _app(regime, "rc_not_this_deployments", database)
    # `pilot` starts only if both startup probes answer True: the served model resolves with
    # its approved card (in CREDIT, the active one) and the journal answers - here, over
    # connections of their own, since nothing has opened the pool yet.
    app = _app(regime, CARD, database)
    rt = app.state.runtime
    relay, pool = rt.relay, rt.lifetime.pool
    assert rt.mode == "pilot" and relay.regime == regime
    assert isinstance(relay.catalog, PgCatalogDirectory) and isinstance(relay.jobs, PgJobStore)
    assert isinstance(relay.stream, PgStreamStore) and pool.closed
    with TestClient(app, client=("127.0.0.1", 50000)) as client:        # the lifespan
        assert not pool.closed
        for probe in rt.lifetime.probes:                                 # now through the pool
            client.portal.call(probe.refresh)
            assert probe.value is True
        assert pool.get_stats()["requests_num"] >= len(rt.lifetime.probes)
        ready = client.get("/readyz")
        assert ready.status_code == 200, ready.text
        assert ready.json() == {"status": "ok", "mode": "pilot",
                                "components": {"price_source": "ok", "journal": "ok"}}
    assert pool.closed


@pytest.fixture(autouse=True)
def _nologin_after():
    """The role is cluster-wide: leave it NOLOGIN, as 0021 made it (test_reads' privilege
    check reads it; DOOR-REVOKE found the order dependence)."""
    yield
    with pgharness.connect("postgres") as admin:
        admin.execute("alter role infrx_runtime nologin password null")


def _runtime_login(database: str) -> str:
    """0021's `infrx_runtime` given LOGIN and a fresh random password, as the operator does
    out of band; the DSN (password inside) lives only in this process's memory."""
    import secrets
    password = secrets.token_urlsafe(24)
    with pgharness.connect(database) as owner:
        owner.execute(f"alter role infrx_runtime login password '{password}'")
    return pgharness.dsn(database).replace(f"postgres:{pgharness.PASSWORD}@",
                                           f"infrx_runtime:{password}@", 1)


def test_f_base__the_pilot_serves_and_the_worker_connects_on_the_dedicated_login(tmp_path):
    """E3C F-1 / R127: on 0021's `infrx_runtime` login (member of no role, so `set role
    service_role` is refused) the gateway composed from settings passes its startup probes,
    serves `/v1/models` and admits one job, and the worker's composition opens its pool and
    hands out a connection - both as that login. Oracle: a pool that SETs the role on the
    dedicated login never connects (the gateway refuses startup, the worker's pool times out)."""
    import asyncio

    import httpx
    from infrx.config import from_env
    from infrx.worker import __main__ as worker_main

    from ..g import relay_support as rs, support as gs

    from infrx.state import pgtesting
    database = fresh()
    with pgharness.connect(database) as owner:     # the consumer, its wallet and key rows
        pgtesting.seed_credit_world(owner, "select 1")     # (the Marlin seed is fresh()'s)
    runtime = _runtime_login(database)
    settings = Settings(supabase_url="https://fake.supabase.co", supabase_key="service-role",
                        pilot=DEFAULTS.replace(infrx_mode="pilot", database_url=runtime,
                                               active_rate_card_version=CARD))
    settings.deployment = settings.deployment.replace(
        accounting_regime="credit", infrx_release_sha="c0ffee" + "0" * 34,
        infrx_image="sha256:" + "b" * 64)
    rows = {__import__("hashlib").sha256(gs.TOKEN.encode()).hexdigest(): rs.CONSUMER_ROW}

    def identities(request):
        key_hash = request.url.params.get("key_hash", "").removeprefix("eq.")
        return httpx.Response(200, json=[rows[key_hash]] if key_hash in rows else [])
    sb = httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                           transport=httpx.MockTransport(identities))
    app = create_app(settings, client=gs.upstream(), sb=sb, objects=InMemoryObjectStore(),
                     index=MemoryScheduler(lambda: None))
    auth = {"authorization": f"Bearer {gs.TOKEN}"}
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/readyz").status_code == 200
        listed = client.get("/v1/models", headers=auth)
        assert listed.status_code == 200, listed.text
        assert gs.PUBLIC_MODEL in {m["id"] for m in listed.json()["data"]}
        admitted = client.post("/v1/jobs", headers={**auth, "idempotency-key": "rl-1"},
                               json={"model": gs.PUBLIC_MODEL, "messages": [
                                   {"role": "user", "content": "hello"}]})
        assert admitted.status_code == 202, admitted.text
    with pgharness.connect(database) as owner:
        assert owner.execute("select count(*) from infrx.jobs").fetchone()[0] >= 1

    cache = tmp_path / "cache"
    cache.mkdir()
    worker_settings = from_env({
        "INFRX_MODE": "pilot", "DATABASE_URL": runtime, "VALKEY_URL": "redis://127.0.0.1:9/0",
        "PROCESSING_CACHE_DIR": str(cache), "S3_MEDIA_BUCKET": "unused",
        "UPSTREAM": "http://127.0.0.1:9", "SUPABASE_URL": "https://fake.supabase.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-for-tests",
        "INFRX_RELEASE_SHA": "c0ffee" + "0" * 34, "INFRX_IMAGE": "sha256:" + "b" * 64,
        "ACCOUNTING_REGIME": "credit", "ACTIVE_RATE_CARD_VERSION": CARD})
    _service, pool = worker_main.compose(worker_settings, objects=InMemoryObjectStore(),
                                         index=MemoryScheduler(lambda: None))

    async def acquire():
        await pool.open(wait=True, timeout=10)
        try:
            async with pool.connection() as conn:
                return (await (await conn.execute("select current_user")).fetchone())[0]
        finally:
            await pool.close()
    assert asyncio.run(acquire()) == "infrx_runtime"
