"""AP-04 (R270): model projects and their immutable serving revisions.

    GET  /lab/v1/control/model-projects                    {data, next_cursor}
    POST /lab/v1/control/model-projects                    201 the project (Idempotency-Key)
    GET  /lab/v1/control/model-projects/{id}/revisions     {data, next_cursor}
    POST /lab/v1/control/model-projects/{id}/revisions     201 the serving revision, or 422
                                                           naming every unsupported reason

The rendering, actor and refusal rules are `lab_artifacts`'s (one `EnvelopeRoute`, one
`guarded`). Nothing mounts while `rt.lab_artifacts` is None.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Query, Request

from ...contracts import api
from ...lab.artifacts.projects import ProjectRequest, RevisionDoc, RevisionRequest, page
from ...lab.artifacts.store import Project
from .. import control
from .lab_artifacts import ERRORS, EnvelopeRoute, IdempotencyKey, guarded

Limit = Annotated[int, Query(ge=1, le=api.MAX_PAGE_SIZE)]
Cursor = Annotated[str | None, Query(max_length=512)]


def register(app: FastAPI, rt: Any) -> None:
    a = getattr(rt, "lab_artifacts", None)
    if a is None:
        return
    router = APIRouter(route_class=EnvelopeRoute, responses=ERRORS)
    base = "/lab/v1/control/model-projects"

    @router.get(base, response_model=api.ListPage[Project], operation_id="listModelProjects")
    async def list_projects(request: Request, limit: Limit = api.DEFAULT_PAGE_SIZE,
                            cursor: Cursor = None):
        async def work(actor):
            return control.ok(page(await a.projects.list(actor), cursor, limit))
        return await guarded(request, rt, work)

    @router.post(base, status_code=201, response_model=Project,
                 operation_id="createModelProject")
    async def create_project(request: Request, body: ProjectRequest, key: IdempotencyKey):
        async def work(actor):
            return control.ok(await a.projects.create(actor, body, key), 201)
        return await guarded(request, rt, work)

    @router.get(base + "/{project_id}/revisions", response_model=api.ListPage[RevisionDoc],
                operation_id="listModelProjectRevisions")
    async def list_revisions(request: Request, project_id: str,
                             limit: Limit = api.DEFAULT_PAGE_SIZE, cursor: Cursor = None):
        async def work(actor):
            docs = await a.projects.revisions(actor, project_id)
            return control.ok(page(docs, cursor, limit))
        return await guarded(request, rt, work)

    @router.post(base + "/{project_id}/revisions", status_code=201, response_model=RevisionDoc,
                 operation_id="createModelProjectRevision")
    async def create_revision(request: Request, project_id: str, body: RevisionRequest,
                              key: IdempotencyKey):
        async def work(actor):
            return control.ok(await a.projects.create_revision(actor, project_id, body, key),
                              201)
        return await guarded(request, rt, work)

    app.include_router(router)
