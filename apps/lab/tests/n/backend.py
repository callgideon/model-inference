#!/usr/bin/env python3
"""N4's real backend for the Lab journey (`journey.test.ts`): the PRODUCTION datasets route
(`infrx.gateway.routes.lab_datasets.router`, WR-N4-1) over 0051's durable import queue
(`PgLabImportJobs`), D7 (`PgLabDataStore`) and L2 (`LabAccess` over `PgAccessStore`) on the
task-local PostgreSQL (an explicit `INFRX_D_TASK`, D7's world of `tests/d/test_d7_lab_data.py`).
Objects are in memory.

WR-AP10C-3: an import is a job on 0051's queue worked by the datasets pool's own pass
(`imports.work`, what `lab.workers`' `import_jobs` runs every IMPORT_PASS_S), driven here by a
door instead of a timer. Test-only: the bearer-token-is-the-user-id stand-in for the verified
Lab session, and the doors `/_test/pass` (one pass; `crash_after_puts` kills the worker
mid-staging, `lose` deletes an import's upload first), `/_test/lapse` (the database clock past
the import lease) and `/_test/revoke` (the grantor's real `lab_revoke_access_grant`).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/n/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "infrx-api"
sys.path.insert(0, str(API))

from fastapi import FastAPI, Request  # noqa: E402


class Dying:
    """The worker's objects: its process 'dies' after `puts` staged writes (the interrupted
    import; the pass counts it `retry` and the lease lapses)."""

    def __init__(self, inner, puts: int) -> None:
        self.inner, self.left = inner, puts

    async def put_if_absent(self, key, data, content_type):
        if self.left == 0:
            raise RuntimeError("the worker process died")
        self.left -= 1
        return await self.inner.put_if_absent(key, data, content_type)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def main() -> None:
    import uvicorn

    from infrx.datasets import imports
    from infrx.gateway.routes import lab_datasets
    from infrx.lab.access import LabAccess
    from infrx.media.store import InMemoryObjectStore
    from infrx.state import migrations
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_data import PgLabDataStore, PgLabImportJobs
    from tests.d import pgharness
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_l2sql_access as l2

    if not os.environ.get("INFRX_D_TASK"):
        raise SystemExit("an explicit task-local key only (INFRX_D_TASK=l4)")
    db = f"{pgharness.DATABASE}_n4"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(db) as conn:
        d7.seed(conn)
    dsn, objects = pgharness.dsn(db), InMemoryObjectStore()
    store, jobs = PgLabDataStore(connector(dsn)), PgLabImportJobs(connector(dsn))

    async def user_of(request: Request) -> str:
        return request.headers.get("authorization", "")[7:]

    app = FastAPI()
    app.include_router(lab_datasets.router(
        access=LabAccess(PgAccessStore(connector(dsn))), store=store, objects=objects,
        user_of=user_of, read=lambda request: request.json(), jobs=jobs))

    @app.post("/_test/pass")
    async def work(body: dict):
        """One pass of the datasets pool (`imports.work`, as lab.workers composes it)."""
        if "lose" in body:
            await objects.delete(imports.rows_key(l2.NEMO, body["lose"]))
        target = Dying(objects, body["crash_after_puts"]) if "crash_after_puts" in body \
            else objects
        return await imports.work(jobs, store, target, worker_id="n4-datasets")

    @app.post("/_test/lapse")
    def lapse():
        """The database clock past the import lease: a silent job is claimed again."""
        with pgharness.connect(db) as conn:
            d7.advance(conn, int(imports.IMPORT_LEASE_S) + 1)
        return {"lapsed": True}

    @app.post("/_test/revoke")
    def revoke():
        """The grantor (C1) revokes NEMO's grant through L2's real RPC (test-only)."""
        with pgharness.connect(db) as conn:
            l2.call(conn, "lab_revoke_access_grant", {
                "actor_user_id": l2.C1, "grantor_org_id": l2.org(conn, l2.C1),
                "recipient_provider_org_id": l2.NEMO})
        return {"revoked": True}
    world = {"provider": l2.NEMO, "other": l2.OTHER, "dev": l2.DEV, "viewer": l2.VIEWER,
             "other_dev": l2.BOTH, "consumer": l2.NOBODY, "grant_ref": d7.W["grant"]}
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    async def serve():
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY {port} {json.dumps(world)}", flush=True)
        await task
    asyncio.run(serve())


if __name__ == "__main__":
    os.chdir(API)
    main()
