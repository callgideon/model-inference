#!/usr/bin/env python3
"""AP-10 10c: the import resume, under the corrected oracle, on ap10's PostgreSQL (0051's
`PgLabImportJobs`, D7's `PgLabDataStore`, the datasets role's composed pass; N1's PG world).

The Lab journey N4-J01 (`apps/lab/tests/n/journey.test.ts`) still resumes an interrupted
import by re-POSTing the same import id over its own in-memory job dict - an oracle of the
pre-0051 backend. With the durable queue the resume is two different things, both asserted
here: an interrupted import (the worker dies mid-staging) is NOT failed - its lease lapses and
the next pass resumes the same job id from the staged chunks to `published`, once; a refused
import is `failed` for good (R243): the same id again answers that failed job, and the resume
is `requeue` under a new id. (WR-AP10C-3 moves the journey to this oracle.)

    INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_import_resume_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import os

import pytest

from infrx.datasets import imports
from infrx.lab.workers import __main__ as lab_workers
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabImportJobs

from ..d import pgharness
from ..d import test_d7_lab_data as d7
from ..n.imports.test_import_pg import DB, spec, world  # noqa: F401
from ..w.test_lab_workers import TRACES, Retention

if os.environ.get("INFRX_D_TASK") != "ap10":
    pytestmark = pytest.mark.skip(reason="PostgreSQL only on the ap10 task-local key")


class Dies:
    """The worker's objects: the process 'dies' after `puts` staged writes."""

    def __init__(self, inner, puts: int) -> None:
        self.inner, self.left = inner, puts

    async def put_if_absent(self, key, data, content_type):
        if self.left == 0:
            raise RuntimeError("the worker process died")
        self.left -= 1
        return await self.inner.put_if_absent(key, data, content_type)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_ap10_import_resumes_by_lease_and_a_refused_one_by_requeue(world, monkeypatch):
    conn, _, objects = world
    dsn = pgharness.dsn(DB)
    jobs = PgLabImportJobs(connector(dsn))
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])
    monkeypatch.setattr(lab_workers, "trace_retention", lambda *a, **k: Retention())

    def role(store):
        worker = lab_workers.compose("datasets", {"LAB_DATABASE_URL": dsn,
                                                  "LAB_WORKER_HEALTH_PORT": "9",
                                                  "LAB_S3_BUCKET": "unused", **TRACES},
                                     objects=store)
        getattr(worker.tasks["import_jobs"](), "close")()      # registers the step, runs nothing
        return steps["import jobs"]

    def enqueue(payload, data):
        return asyncio.run(imports.enqueue(jobs, objects, {"spec": payload, "body": data.decode()},
                                           provider_org_id=d7.NEMO, actor="dev@nemo"))

    # an interrupted import: not failed; the lease lapses and the same id resumes, once
    payload, data = spec("benchmark", import_id="b0000001-0000-4000-8000-00000000ca01")
    job_id = enqueue(payload, data)["job_id"]
    assert asyncio.run(role(Dies(objects, 2))()) == {"succeeded": 0, "failed": 0, "retry": 1}
    assert asyncio.run(jobs.job(job_id, provider_org_id=d7.NEMO))["state"] == "running"
    staged = len(asyncio.run(objects.keys("")))
    d7.advance(conn, int(imports.IMPORT_LEASE_S) + 1)
    assert asyncio.run(role(objects)()) == {"succeeded": 1, "failed": 0, "retry": 0}
    done = asyncio.run(jobs.job(job_id, provider_org_id=d7.NEMO))
    assert (done["state"], done["attempts"], done["result"]["accepted"]) == ("succeeded", 2, 5)
    assert len(asyncio.run(objects.keys(""))) > staged
    assert conn.execute("select count(*) from infrx.lab_records where ref = %s",
                        (done["result"]["dataset_ref"],)).fetchone()[0] == 1
    assert enqueue(payload, data)["state"] == "succeeded"        # the same id: the same job

    # a refused import is failed for good (R243); the resume is a requeue under a new id
    payload, data = spec("benchmark", import_id="b0000001-0000-4000-8000-00000000ca02",
                         dataset_id="da000000-0000-4000-8000-00000000ca02")
    failed = enqueue(payload, data)["job_id"]
    asyncio.run(objects.delete(imports.rows_key(d7.NEMO, failed)))    # the upload is gone
    assert asyncio.run(role(objects)()) == {"succeeded": 0, "failed": 1, "retry": 0}
    asyncio.run(objects.put_if_absent(imports.rows_key(d7.NEMO, failed), data,
                                      "application/x-ndjson"))
    before = conn.execute("select count(*) from infrx.lab_import_jobs").fetchone()[0]
    assert enqueue(payload, data)["state"] == "failed", "a re-POST resumed a failed id"
    assert asyncio.run(role(objects)()) == {"succeeded": 0, "failed": 0, "retry": 0}
    assert conn.execute("select count(*) from infrx.lab_import_jobs").fetchone()[0] == before
    again = "b0000001-0000-4000-8000-00000000ca03"
    asyncio.run(objects.put_if_absent(imports.rows_key(d7.NEMO, again), data,
                                      "application/x-ndjson"))
    asyncio.run(jobs.requeue(failed, new_job_id=again, provider_org_id=d7.NEMO,
                             actor="dev@nemo"))
    assert asyncio.run(role(objects)()) == {"succeeded": 1, "failed": 0, "retry": 0}
    assert asyncio.run(jobs.job(again, provider_org_id=d7.NEMO))["state"] == "succeeded"
    assert asyncio.run(jobs.job(failed, provider_org_id=d7.NEMO))["state"] == "failed"
