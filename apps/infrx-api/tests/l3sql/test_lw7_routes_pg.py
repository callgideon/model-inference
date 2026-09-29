#!/usr/bin/env python3
"""lab-sql LW7's routes on real PostgreSQL: `/lab/v1/optimizations` over `PgLabVariants`
(WR-C6-VARIANTS: the listing instead of 503) with L2's `LabAccess`, and `POST
imports/{id}/requeue` as `LAB_DATASETS` composes it (`pilot._lab`, 0051/0055's
`PgLabImportJobs`) with the datasets role's pass working the new job (WR-C6-REQUEUE). Only
the session verifier (a token per user) and the objects (in memory) are stand-ins. Outside the mutant runner: the oracles are the SQL list
(`tests/d/test_code_mutants_lw7.py`) and the ports' (`tests/l3sql/mutants.py`).

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw7_routes_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from infrx.contracts import errors
from infrx.datasets import imports
from infrx.gateway import pilot
from infrx.gateway.routes import lab_datasets as ld
from infrx.gateway.routes import lab_releases as lr
from infrx.lab.workers import __main__ as lab_workers
from infrx.lab.access import LabAccess
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_variants import PgLabVariants

from ..d import pgharness
from ..d import test_d7_variant as v
from ..d import test_l2sql_access as l2
from ..g import support as gsupport
from ..g.lab_datasets.test_lab_datasets import base, call
from ..g.lab_releases.test_lab_releases import Sessions, token
from ..n.imports.test_import_pg import DB as N1_DB
from ..n.imports.test_import_pg import spec, world  # noqa: F401
from ..w.test_lab_workers import TRACES, Retention

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


def test_lw7_routes_pg__a_failed_import_is_requeued_and_the_datasets_role_publishes_it(
        world, monkeypatch):
    """The route requeues the provider's failed job as a new job naming it (the failed row
    stays failed); a retry is the same job; the datasets role's pass imports the new job
    unchanged and it reads `published`; a developer of another provider gets a 404."""
    conn, _, objects = world
    dsn = pgharness.dsn(N1_DB)
    settings = gsupport.settings(deployment=dataclasses.replace(gsupport.BUILD,
                                                                lab_datasets=True))
    x = pilot._lab(settings, connect=connector(dsn), objects=objects)["lab_datasets"]
    x = dataclasses.replace(x, sessions=Sessions((l2.DEV, l2.BOTH)))
    app = FastAPI()
    ld.register(app, gsupport.runtime(), x)
    payload, data = spec("benchmark", import_id="b0000001-0000-4000-8000-00000000c701")
    path = base(v.NEMO) + "/imports"
    old = payload["import_id"]
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])
    monkeypatch.setattr(lab_workers, "trace_retention", lambda *a, **k: Retention())
    role = lab_workers.compose("datasets", {"LAB_DATABASE_URL": dsn,
                                            "LAB_WORKER_HEALTH_PORT": "9",
                                            "LAB_S3_BUCKET": "unused", **TRACES},
                               objects=objects)
    role.tasks["import_jobs"]().close()
    run = imports.Importer.run

    async def gone(self, *args, **kw):
        raise errors.NotFound("the source is gone")
    with TestClient(app, raise_server_exceptions=False) as client:
        assert call(client, l2.DEV, "POST", path, {"spec": payload,
                                                   "body": data.decode()}).status_code == 200
        monkeypatch.setattr(imports.Importer, "run", gone)
        assert asyncio.run(steps["import jobs"]()) == {"succeeded": 0, "failed": 1, "retry": 0}
        monkeypatch.setattr(imports.Importer, "run", run)
        assert call(client, l2.DEV, "GET", f"{path}/{old}").json()["state"] == "failed"
        made = call(client, l2.DEV, "POST", f"{path}/{old}/requeue")
        assert made.status_code == 200, made.text
        new = made.json()["import_id"]
        assert call(client, l2.DEV, "POST", f"{path}/{old}/requeue").json()["import_id"] == new
        assert conn.execute("select requeued_from::text, created_by from infrx.lab_import_jobs "
                            "where job_id = %s", (new,)).fetchone() == (old, l2.DEV)
        assert asyncio.run(steps["import jobs"]()) == {"succeeded": 1, "failed": 0, "retry": 0}
        job = call(client, l2.DEV, "GET", f"{path}/{new}").json()
        assert (job["state"], job["report"]["accepted"]) == ("published", 5), job
        assert call(client, l2.DEV, "GET", f"{path}/{old}").json()["state"] == "failed"
        foreign = call(client, l2.BOTH, "POST", base(v.OTHER) + f"/imports/{old}/requeue")
        assert foreign.status_code == 404, foreign.text
