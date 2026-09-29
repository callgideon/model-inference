#!/usr/bin/env python3
"""lab-sql LW7's routes on real PostgreSQL: `/lab/v1/optimizations` over `PgLabVariants`
(WR-C6-VARIANTS: the listing instead of 503) with L2's `LabAccess`. Only the session verifier
(a token per user) is a stand-in. Outside the mutant runner: the oracles are the SQL list
(`tests/d/test_code_mutants_lw7.py`) and the ports' (`tests/l3sql/mutants.py`).

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw7_routes_pg.py
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from infrx.gateway.routes import lab_releases as lr
from infrx.lab.access import LabAccess
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_variants import PgLabVariants

from ..d import pgharness
from ..d import test_d7_variant as v
from ..d import test_l2sql_access as l2
from ..g import support as gsupport
from ..g.lab_releases.test_lab_releases import Sessions, token

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lw7r"


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        v.seed(connection)
        yield connection


def test_lw7_routes_pg__optimizations_lists_the_providers_variants_not_a_503(conn):
    """A viewer of NEMO reads NEMO's variants with the newest comparison (never OTHER's); a
    developer of OTHER, whose provider has none, reads an empty listing - a 200, not a 503;
    no membership of the named provider is a 404."""
    report = v.stored_report(conn, 0x91)
    mine, bare = v.variant(conn, 0x91), v.variant(conn, 0x92)
    digest = v.ok(conn, "lab_put_variant_comparison", v.put(v.comparison(mine, report)))
    connect = connector(pgharness.dsn(DB))
    x = lr.LabReleases(Sessions((l2.VIEWER, l2.BOTH)), LabAccess(PgAccessStore(connect)),
                       records=PgLabVariants(connect))
    app = FastAPI()
    lr.register(app, gsupport.runtime(), x)
    c = TestClient(app, raise_server_exceptions=False)

    def page(user, provider):
        return c.get(lr.OPTIMIZATIONS_PATH, params={"provider_org_id": provider},
                     headers={"authorization": f"Bearer {token(user)}"})

    shown = page(l2.VIEWER, v.NEMO)
    assert shown.status_code == 200, shown.text
    rows = shown.json()["data"]
    assert [r["variant_ref"] for r in rows] == [mine, bare]
    assert rows[0]["comparison"]["comparison_digest"] == digest["comparison_digest"]
    assert (rows[0]["comparison"]["outcome"], rows[1]["comparison"]) == ("equivalent", None)
    empty = page(l2.BOTH, v.OTHER)
    assert (empty.status_code, empty.json()) == (200, {"data": []}), empty.text
    assert page(l2.BOTH, v.NEMO).status_code == 404
