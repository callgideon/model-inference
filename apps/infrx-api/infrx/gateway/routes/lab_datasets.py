"""WR-N4-1: the Lab's datasets surface, `/lab/v1/providers/{provider}/datasets`, over N1/N2/N3.

    POST imports/preview  POST imports  GET imports/{id}  GET versions  GET versions/{ref}
    POST versions         POST exports  GET exports/{id}/parts/{n}

`router()` is N4's proposed production router (`apps/lab/tests/n/backend.py`, the Lab journey's
backend) moved here, with three changes for a public process: the user is the verified Lab
session (`lab_auth.authenticate`: the forwarded Supabase session through the project's auth
server, R175 - WR-N-3: a bundle upload's provider identity at the gateway IS that session,
never an API key), every body is read only after the acting provider was derived (WR-N-2)
and within a byte bound, and a malformed body is a 400, not a 500. Refusals keep the
backend's `{detail, report?, leaks?}` shape the Lab's `httpDatasets` port reads.

Stores: D7 (`PgLabDataStore`), L2 (`LabAccess` over `PgAccessStore`) and the Lab objects -
the media store under `lab/<provider>/` (R182). Mounted only when the composition put a
`LabDatasets` on `rt.lab_datasets` (LAB_DATASETS, off).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ...contracts import errors
from ...datasets import acting_provider, imports, lineage, versions
from .. import lab_auth
from . import intake

PREFIX = "/lab/v1/providers/{provider}/datasets"
#: One import is one bounded request. ponytail: a streamed bundle upload when datasets
#: outgrow it.
MAX_BODY_BYTES = 64 * 2**20
STATUS = ((errors.InvalidApiKey, 401), (imports.ImportRejected, 400),
          (errors.RequestTooLarge, 413), (errors.InvalidRequest, 400), (errors.Forbidden, 403), (errors.NotFound, 404),
          (errors.Conflict, 409), (errors.Gone, 410))


@dataclass(frozen=True)
class LabDatasets:
    sessions: lab_auth.Sessions
    access: object                          # infrx.lab.access.LabAccess
    store: object                           # D7: PgLabDataStore
    objects: object                         # the Lab objects (the media store, lab/<p>/)


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


def router(*, access, store, objects, user_of, read, clock=lambda: datetime.now(UTC),
           objects_for=None) -> APIRouter:
    """The datasets surface. Every call first derives the acting provider (WR-N-2);
    `user_of(request)` is the verified user, `read(request)` the bounded JSON body."""
    api = APIRouter(prefix=PREFIX)
    jobs: dict[tuple[str, str], dict] = {}
    published: dict[str, list[str]] = {}          # ponytail: WR-N4-2 asks D7 for a list RPC
    # ponytail: imports run as tasks of this process; the I5 datasets pool (a durable
    # import-job queue, WR-N4-3) when one exists. A re-POST of an import id resumes it.
    tasks: set = set()

    def note(provider: str, ref: str) -> None:
        if ref not in published.setdefault(provider, []):
            published[provider].append(ref)

    async def guarded(request: Request, provider: str, work):
        """`work(provider, user)` for the acting provider (WR-N-2); refusals as statuses."""
        try:
            user = await user_of(request)
            return await work(await acting_provider(access, user, provider), user)
        except errors.DomainError as refused:
            return refusal(refused)
        except (KeyError, TypeError, AttributeError):
            return refusal(errors.InvalidRequest("the body is not this operation's"))
        except Exception as failed:             # noqa: BLE001 - LDP-F3: a store fault is a
            lab_auth.log.error("lab datasets route failed: %s", type(failed).__name__)  # 503
            return refusal(errors.DependencyUnavailable("the datasets store failed"))

    @api.post("/imports/preview")
    async def preview(request: Request, provider: str):
        async def work(provider, user):
            body = await read(request)
            return imports.preview(body["spec"], body["head"].encode(), provider_org_id=provider)
        return await guarded(request, provider, work)

    @api.post("/imports")
    async def start(request: Request, provider: str):
        async def work(provider, user):
            body = await read(request)
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
            task = asyncio.get_running_loop().create_task(run())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
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
        async def work(provider, user):
            body = await read(request)
            policy = body["policy"]
            derived = await versions.derive(
                store, objects, provider_org_id=provider, actor=user,
                dataset_id=body["dataset_id"], version=body["version"],
                created_at=clock().strftime("%Y-%m-%dT%H:%M:%SZ"), base=body.get("base"),
                add=body.get("add", []), now=clock(), policy=versions.SplitPolicy(
                    seed=policy["seed"], train_bp=policy["train_bp"],
                    validation_bp=policy["validation_bp"]))
            note(provider, derived.dataset_ref)
            return vars(derived)
        return await guarded(request, provider, work)

    @api.post("/exports")
    async def export(request: Request, provider: str):
        async def work(provider, user):
            body = await read(request)
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


def register(app, rt, datasets: LabDatasets | None = None):
    """Mount the datasets routes over `datasets` (default `rt.lab_datasets`); without one
    nothing is mounted and `None` is returned."""
    x = datasets if datasets is not None else getattr(rt, "lab_datasets", None)
    if x is None:
        return None
    limits = rt.settings.pilot

    async def read(request: Request) -> dict:
        raw = await intake.read_body(request, max_bytes=MAX_BODY_BYTES,
                                     timeout_s=limits.intake_timeout_s, clock=rt.clock)
        try:
            return json.loads(raw)            # not an object: the operation's 400 below
        except ValueError:
            raise errors.InvalidRequest("the body is not JSON") from None

    api = router(access=x.access, store=x.store, objects=x.objects, read=read,
                 user_of=lambda request: lab_auth.authenticate(request, x.sessions))
    for route in api.routes:              # on the app's own table, as every other router
        app.add_api_route(route.path, route.endpoint, methods=list(route.methods))
    return x
