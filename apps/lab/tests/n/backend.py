#!/usr/bin/env python3
"""N4's real backend for the Lab journey (`journey.test.ts`): the datasets HTTP surface the Lab's
`httpDatasets` port calls, composed over the REAL N1/N2/N3 modules, D7 (`PgLabDataStore`) and L2
(`LabAccess` over `PgAccessStore`) on the task-local PostgreSQL (`INFRX_D_TASK=n3`, 0001-0030,
the D7 world of `tests/d/test_d7_lab_data.py`). Objects are in memory.

`router()` is the proposed production route (WR-N4-1: mount it in the gateway behind a flag that
defaults off, with the Lab's verified session in place of `user_of`); only `main()` - the task-local
database, the bearer-token-is-the-user-id stand-in and the `_crash_after_puts` knob - is test-only.
Long imports run as backend jobs: POST returns `running` at once and the importer runs as a task.

    INFRX_D_TASK=n3 uv run --frozen --project apps/infrx-api python apps/lab/tests/n/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "infrx-api"
sys.path.insert(0, str(API))

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, PlainTextResponse  # noqa: E402

from infrx.contracts import errors  # noqa: E402
from infrx.datasets import acting_provider, imports, lineage, versions  # noqa: E402

STATUS = ((imports.ImportRejected, 400), (errors.InvalidRequest, 400), (errors.Forbidden, 403),
          (errors.NotFound, 404), (errors.Conflict, 409), (errors.Gone, 410))


def refusal(error: Exception) -> JSONResponse:
    status = next((code for kind, code in STATUS if isinstance(error, kind)), 503)
    body: dict = {"detail": str(error) if status != 503 else "the datasets service failed"}
    if isinstance(error, versions.LeakRefused):
        body["leaks"] = error.leaks
    if isinstance(error, imports.ImportRejected):
        body["report"] = vars(error.report)
    return JSONResponse(body, status_code=status)


async def pieces(text: str):
    data = text.encode()
    for start in range(0, len(data), 4096):
        yield data[start:start + 4096]


def router(*, access, store, objects, user_of, clock=lambda: datetime.now(UTC),
           objects_for=None) -> APIRouter:
    """The datasets surface. Every call first derives the acting provider (WR-N-2)."""
    api = APIRouter(prefix="/lab/v1/providers/{provider}/datasets")
    jobs: dict[tuple[str, str], dict] = {}
    published: dict[str, list[str]] = {}          # ponytail: WR-N4-2 asks D7 for a list RPC
    tasks: set = set()                             # ponytail: a durable job queue (I5) in prod

    def note(provider: str, ref: str) -> None:
        if ref not in published.setdefault(provider, []):
            published[provider].append(ref)

    async def guarded(request: Request, provider: str, work):
        """`work(provider, user)` for the acting provider (WR-N-2); refusals as statuses."""
        try:
            return await work(await acting_provider(access, user_of(request), provider),
                              user_of(request))
        except errors.DomainError as refused:
            return refusal(refused)

    @api.post("/imports/preview")
    async def preview(request: Request, provider: str):
        body = await request.json()

        async def work(provider, user):
            return imports.preview(body["spec"], body["head"].encode(), provider_org_id=provider)
        return await guarded(request, provider, work)

    @api.post("/imports")
    async def start(request: Request, provider: str):
        body = await request.json()

        async def work(provider, user):
            spec = imports.parse_spec(body["spec"], provider_org_id=provider)
            key = (provider, spec.import_id)
            if jobs.get(key, {}).get("state") in ("running", "published"):
                return jobs[key]
            job = jobs[key] = {"import_id": spec.import_id, "state": "running", "report": None,
                               "error": None}
            target = objects_for(body) if objects_for else objects

            async def run():
                try:
                    report = await imports.Importer(store, target).run(
                        body["spec"], pieces(body["body"]), provider_org_id=provider,
                        actor=user, accept_rejects=bool(body.get("accept_rejects")))
                    note(provider, report.dataset_ref)
                    job.update(state="published", report=vars(report))
                except imports.ImportRejected as rejected:
                    job.update(state="rejected", report=vars(rejected.report))
                except BaseException as died:            # noqa: BLE001 - the job records it
                    job.update(state="failed",
                               error=f"the import stopped ({type(died).__name__})")
            tasks.add(asyncio.get_running_loop().create_task(run()))
            return dict(job)
        return await guarded(request, provider, work)

    @api.get("/imports/{import_id}")
    async def job(request: Request, provider: str, import_id: str):
        async def work(provider, user):
            found = jobs.get((provider, import_id))
            if found is None:
                raise errors.NotFound(f"no import {import_id}")
            return found
        return await guarded(request, provider, work)

    @api.get("/versions")
    async def listing(request: Request, provider: str):
        async def work(provider, user):
            out = []
            for ref in published.get(provider, []):
                m = await store.resolve(ref, provider_org_id=provider)
                out.append({"dataset_ref": ref, "dataset_id": m.dataset_id,
                            "version": m.version, "derivation": m.derivation,
                            "samples": len(m.samples)})
            return out
        return await guarded(request, provider, work)

    @api.get("/versions/{ref:path}")
    async def version(request: Request, provider: str, ref: str):
        async def work(provider, user):
            return await lineage.status(store, objects, ref, provider_org_id=provider,
                                        now=clock())
        return await guarded(request, provider, work)

    @api.post("/versions")
    async def derive(request: Request, provider: str):
        body = await request.json()

        async def work(provider, user):
            policy = body["policy"]
            derived = await versions.derive(
                store, objects, provider_org_id=provider, actor=user,
                dataset_id=body["dataset_id"], version=body["version"],
                created_at=clock().strftime("%Y-%m-%dT%H:%M:%SZ"), base=body.get("base"), add=body.get("add", []),
                now=clock(), policy=versions.SplitPolicy(
                    seed=policy["seed"], train_bp=policy["train_bp"],
                    validation_bp=policy["validation_bp"]))
            note(provider, derived.dataset_ref)
            return vars(derived)
        return await guarded(request, provider, work)

    @api.post("/exports")
    async def export(request: Request, provider: str):
        body = await request.json()

        async def work(provider, user):
            return await versions.export(
                store, objects, provider_org_id=provider, dataset_ref=body["dataset_ref"],
                export_id=body["export_id"], now=clock(), ttl_s=body["ttl_s"])
        return await guarded(request, provider, work)

    @api.get("/exports/{export_id}/parts/{part}")
    async def part(request: Request, provider: str, export_id: str, part: int):
        async def work(provider, user):
            data = await versions.read_part(store, objects, provider_org_id=provider,
                                            export_id=export_id, part=part, now=clock())
            return PlainTextResponse(data.decode(), media_type="application/x-ndjson")
        return await guarded(request, provider, work)

    return api


# --- test-only composition ---------------------------------------------------------------------
class Dying:
    """An object store whose process 'dies' after `puts` writes (the interrupted import)."""

    def __init__(self, inner, puts: int) -> None:
        self.inner, self.left = inner, puts

    async def put_if_absent(self, key, data, content_type):
        if self.left == 0:
            raise SystemExit("interrupted")
        self.left -= 1
        return await self.inner.put_if_absent(key, data, content_type)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def main() -> None:
    import uvicorn

    from infrx.lab.access import LabAccess
    from infrx.media.store import InMemoryObjectStore
    from infrx.state import migrations
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_data import PgLabDataStore
    from tests.d import pgharness
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_l2sql_access as l2

    if not os.environ.get("INFRX_D_TASK"):
        raise SystemExit("an explicit task-local key only (INFRX_D_TASK=n3)")
    db = f"{pgharness.DATABASE}_n4"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(db) as conn:
        d7.seed(conn)
    dsn, objects = pgharness.dsn(db), InMemoryObjectStore()
    app = FastAPI()
    app.include_router(router(
        access=LabAccess(PgAccessStore(connector(dsn))), store=PgLabDataStore(connector(dsn)),
        objects=objects, user_of=lambda r: r.headers.get("authorization", "")[7:],
        objects_for=lambda body: Dying(objects, body["_crash_after_puts"])
        if "_crash_after_puts" in body else objects))
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
