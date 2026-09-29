#!/usr/bin/env python3
"""WR-N4-3's real half: the datasets role's import-job pass (`python -m infrx.lab.workers
datasets`, composed from its env) over 0051's `PgLabImportJobs` and D7's `PgLabDataStore` on
the n3 PostgreSQL (57518; N1's world, `tests/n/imports/test_import_pg.py`, D7's seeded grant),
the Lab objects in memory (or MinIO on n3's 57519 with INFRX_M_S3_ENDPOINT).

enqueue (the route's half: rows write-once, one job per import id) -> a worker claims it,
heartbeats and dies -> another worker's heartbeat is refused while the lease holds -> the DB
clock passes the lease -> the composed role's pass reclaims it and finishes it `succeeded`
with the published dataset -> the dead worker's late finish is refused. Outside the mutant
runner; the oracles are `tests/n/imports`' `n4_*` and `tests/w`'s `lw_import_jobs_*`.

    INFRX_D_TASK=n3 uv run --frozen pytest -q tests/w/test_lab_workers_imports_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.datasets import imports
from infrx.lab.workers import __main__ as lab_workers
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabImportJobs

from ..d import pgharness
from ..d import test_d7_lab_data as d7
from ..n.imports.test_import_pg import DB, pytestmark, spec, world  # noqa: F401
from .test_lab_workers import TRACES, Retention


def test_lab_workers_imports_pg__a_lost_lease_is_reclaimed_and_the_import_finished_once(
        world, monkeypatch):
    conn, store, objects = world
    dsn = pgharness.dsn(DB)
    jobs = PgLabImportJobs(connector(dsn))
    payload, data = spec("benchmark", import_id="b0000001-0000-4000-8000-00000000c501")
    body = {"spec": payload, "body": data.decode()}
    first = asyncio.run(imports.enqueue(jobs, objects, body, provider_org_id=d7.NEMO,
                                        actor="dev@nemo"))
    again = asyncio.run(imports.enqueue(jobs, objects, body, provider_org_id=d7.NEMO,
                                        actor="dev@nemo"))
    assert first == again and first["state"] == "queued"
    (held,) = asyncio.run(jobs.claim(limit=5, worker_id="dead", redelivery_s=60))
    asyncio.run(jobs.heartbeat(held["job_id"], worker_id="dead"))
    with pytest.raises(errors.StateConflict):
        asyncio.run(jobs.heartbeat(held["job_id"], worker_id="other"))
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])
    monkeypatch.setattr(lab_workers, "trace_retention", lambda *a, **k: Retention())
    worker = lab_workers.compose("datasets", {"LAB_DATABASE_URL": dsn,
                                              "LAB_WORKER_HEALTH_PORT": "9",
                                              "LAB_S3_BUCKET": "unused", **TRACES},
                                 objects=objects)
    worker.tasks["import_jobs"]().close()
    assert asyncio.run(steps["import jobs"]()) == {"succeeded": 0, "failed": 0, "retry": 0}
    d7.advance(conn, int(imports.IMPORT_LEASE_S) + 1)                # the dead worker's lease lapses
    assert asyncio.run(steps["import jobs"]()) == {"succeeded": 1, "failed": 0, "retry": 0}
    done = asyncio.run(jobs.job(held["job_id"], provider_org_id=d7.NEMO))
    assert (done["state"], done["attempts"]) == ("succeeded", 2)
    assert done["result"]["accepted"] == 5 and done["result"]["dataset_ref"]
    assert conn.execute("select count(*) from infrx.lab_records where ref = %s",
                        (done["result"]["dataset_ref"],)).fetchone()[0] == 1
    with pytest.raises(errors.StateConflict):
        asyncio.run(jobs.finish(held["job_id"], "failed", worker_id="dead", error="late"))

