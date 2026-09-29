#!/usr/bin/env python3
"""lab-sql LW9's reads through the launched composition (`pilot.lab_releases`) on real
PostgreSQL: `/lab/v1/optimizations` shows the two identities R3 stored (WR-LW7-3a, null while
not stored) and `/lab/v1/releases`' `progress.assignments` is D9's per-serving tally
(WR-C7-TALLY). Only the session verifier (a token per user) and the objects (in memory, the
plan stored as the release launcher stores it) are stand-ins. Outside the mutant runner: the
oracles are the SQL list (`tests/d/test_code_mutants_lw9.py`), the ports'
(`tests/l3sql/mutants.py`) and the page's (`tests/g/lab_releases/mutants.py`).

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw9_routes_pg.py
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from infrx.gateway import pilot
from infrx.gateway.routes import lab_releases as lr
from infrx.lab.access import LabAccess
from infrx.lab.workers.__main__ import plan_key
from infrx.media.store import InMemoryObjectStore
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore

from ..d import checks_credit as cc
from ..d import pgharness
from ..d import test_code_mutants_live as lv
from ..d import test_d7_variant as v
from ..d import test_l2sql_access as l2
from ..g import support as gsupport
from ..g.lab_releases.test_lab_releases import Sessions, token
from ..r.control.test_control import plan
from .test_lw9 import BASE, NVFP4, put

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lw9r"


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        v.seed(connection)
        yield connection


def test_lw9_routes_pg__identities_and_the_tally_reach_the_lab_pages(conn):
    """A viewer of NEMO reads a variant's stored identities (the other variant's null), and a
    release's progress whose assignments are its terminal requests per serving and pin."""
    identified, bare = v.variant(conn, 0x91), v.variant(conn, 0x92)
    v.ok(conn, "lab_put_variant_identities", put(identified))
    candidate = lv.ref_of(conn, cc.DEV_DEPLOYMENT)
    ref, body = lv.launch(conn, 0x94, candidate)
    for i, state in enumerate(("succeeded", "failed", "queued"), start=1):
        lv.job(conn, ref, body, v.uid(i, 0x94c), candidate, state)
    lv.job(conn, ref, body, v.uid(4, 0x94c), body["baseline_ref"], "succeeded")
    conn.commit()
    objects = InMemoryObjectStore()
    asyncio.run(objects.put_if_absent(plan_key(v.NEMO, body["policy_id"]),
                                      plan().model_dump_json().encode(), "application/json"))
    connect = connector(pgharness.dsn(DB))
    x = pilot.lab_releases(connect, Sessions((l2.VIEWER,)), LabAccess(PgAccessStore(connect)),
                           objects)
    app = FastAPI()
    lr.register(app, gsupport.runtime(), x)
    c = TestClient(app, raise_server_exceptions=False)

    def page(path):
        answer = c.get(path, params={"provider_org_id": v.NEMO},
                       headers={"authorization": f"Bearer {token(l2.VIEWER)}"})
        assert answer.status_code == 200, answer.text
        return answer.json()["data"]

    rows = {r["variant_ref"]: r for r in page(lr.OPTIMIZATIONS_PATH)}
    assert (rows[identified]["base"], rows[identified]["variant"]) == (BASE, NVFP4)
    assert (rows[bare]["base"], rows[bare]["variant"]) == (None, None)
    [release] = [r for r in page(lr.RELEASES_PATH)["releases"] if r["policy_ref"] == ref]
    progress = release["progress"]
    assert sorted(progress["assignments"], key=lambda a: a["serving_ref"]) == sorted(
        [{"serving_ref": candidate, "pinned_by": "cohort", "requests": 2},
         {"serving_ref": body["baseline_ref"], "pinned_by": "cohort", "requests": 1}],
        key=lambda a: a["serving_ref"]), progress
    assert progress["candidate"]["requests"] + progress["baseline"]["requests"] == 3
