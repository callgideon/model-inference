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
