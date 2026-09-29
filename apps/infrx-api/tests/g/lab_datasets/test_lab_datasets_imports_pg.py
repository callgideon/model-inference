#!/usr/bin/env python3
"""WR-C5-N4-ROUTE's real half: the datasets surface as `LAB_DATASETS` composes it
(`pilot._lab`: D7's `PgLabDataStore`, 0051's `PgLabImportJobs` and L2's `LabAccess` on one
pool, the Lab objects) on the n3 PostgreSQL (N1's world, D7's seeded grant), and the datasets
role's pass (`python -m infrx.lab.workers datasets`, composed from its env) working the job.
Only the session verifier (a token per user) and the objects (in memory) are stand-ins.

POST imports enqueues one job (a re-POST is the same job) and publishes nothing; the role's
pass works it; GET reads it `published` with its report, then the version is listed; another
provider's developer polling it is a 404. Outside the mutant runner: the oracles are
`tests/g/lab_datasets`' `import_*` mutants and `tests/g/mutants.py`'s `lab_datasets_jobs_*`.

    INFRX_D_TASK=n3 uv run --frozen pytest -q tests/g/lab_datasets/test_lab_datasets_imports_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import dataclasses

from fastapi import FastAPI
from fastapi.testclient import TestClient
from infrx.gateway import pilot
from infrx.gateway.routes import lab_datasets as ld
from infrx.lab.workers import __main__ as lab_workers
from infrx.state.jobstore import connector

from .. import support
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from ...n.imports.test_import_pg import DB, pytestmark, spec, world  # noqa: F401
from ...w.test_lab_workers import TRACES, Retention
from .test_lab_datasets import Sessions, base, call


def test_lab_datasets_imports_pg__the_route_enqueues_and_the_datasets_role_publishes(
        world, monkeypatch):
    conn, store, objects = world
    dsn = pgharness.dsn(DB)
    settings = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                               lab_datasets=True))
    x = pilot._lab(settings, connect=connector(dsn), objects=objects)["lab_datasets"]
    x = dataclasses.replace(x, sessions=Sessions((l2.DEV, l2.BOTH)))
    app, rt = FastAPI(), support.runtime()
    ld.register(app, rt, x)
    payload, data = spec("benchmark", import_id="b0000001-0000-4000-8000-00000000c601")
    path = base(d7.NEMO) + "/imports"
    job_path = f"{path}/{payload['import_id']}"
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
    with TestClient(app, raise_server_exceptions=False) as client:
        body = {"spec": payload, "body": data.decode()}
        for _ in range(2):
            started = call(client, l2.DEV, "POST", path, body)
            assert started.status_code == 200, started.text
            assert started.json()["state"] == "running"
        assert conn.execute("select count(*), min(created_by) from infrx.lab_import_jobs "
                            "where job_id = %s", (payload["import_id"],)).fetchone() == \
            (1, l2.DEV)
        assert call(client, l2.DEV, "GET", job_path).json()["state"] == "running"
        assert asyncio.run(steps["import jobs"]()) == {"succeeded": 1, "failed": 0, "retry": 0}
        job = call(client, l2.DEV, "GET", job_path).json()
        assert (job["state"], job["error"], job["report"]["accepted"]) == ("published", None, 5)
        listed = call(client, l2.DEV, "GET", base(d7.NEMO) + "/versions").json()
        assert [v["dataset_ref"] for v in listed] == [job["report"]["dataset_ref"]]
        foreign = call(client, l2.BOTH, "GET", base(d7.OTHER) + "/imports/"
                       + payload["import_id"])
        assert foreign.status_code in (403, 404) and "dataset_ref" not in foreign.text
