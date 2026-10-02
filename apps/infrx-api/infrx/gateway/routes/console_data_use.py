"""AP-07a: the grantor's data-use routes (R270), over `infrx.console.data_use.DataUse`.

    GET    /console/v1/data-use                -> DataUseDoc (consent head, keys, grants)
    PUT    /console/v1/keys/{key_id}/capture   -> DataUseDoc (a new consent version, or a replay)
    GET    /console/v1/data-grants             -> {data: [Grant], next_cursor: null}
    POST   /console/v1/data-grants             -> 201 Grant (the next version), 200 on a replay
    DELETE /console/v1/data-grants/{grant_id}  -> Grant (revoked; a second DELETE replays)

The actor is `rt.actors`' (a verified session); the organization is the actor's. Mounted only
when the composition put a `DataUse` on `rt.data_use` (off by default). Every failure is the R270
envelope, a body that does not validate included (`control.R270Route`). Replays are state-based -
the same decision is answered from the current state without a new version - so no
`Idempotency-Key` store is needed here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from ...console import data_use
from ...console.session import web_session
from ...contracts import api
from .. import control

DATA_USE_PATH = "/console/v1/data-use"
KEYS_PATH = "/console/v1/keys"
GRANTS_PATH = "/console/v1/data-grants"


def register(app: FastAPI, rt: Any) -> data_use.DataUse | None:
    service = getattr(rt, "data_use", None)
    if service is None:
        return None
    router = APIRouter(route_class=control.R270Route)

    async def answer(request: Request, work, status: int = 200) -> JSONResponse:
        result = await work(web_session(await rt.actors.actor(request)))   # R272: a web door takes no API key
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], bool):
            result, created = result
            status = 201 if created else 200
        return control.ok(result, status)

    @router.get(DATA_USE_PATH, response_model=data_use.DataUseDoc)
    async def read_data_use(request: Request):
        return await answer(request, service.read)

    @router.put(KEYS_PATH + "/{key_id}/capture", response_model=data_use.DataUseDoc)
    async def put_capture(request: Request, key_id: str, body: data_use.CaptureRequest):
        return await answer(request, lambda actor: service.put_capture(actor, key_id, body))

    @router.get(GRANTS_PATH, response_model=api.ListPage[data_use.Grant])
    async def list_grants(request: Request):
        async def page(actor):
            return api.ListPage[data_use.Grant](data=await service.grants(actor))
        return await answer(request, page)

    @router.post(GRANTS_PATH, response_model=data_use.Grant, status_code=201)
    async def put_grant(request: Request, body: data_use.GrantRequest):
        return await answer(request, lambda actor: service.put_grant(actor, body))

    @router.delete(GRANTS_PATH + "/{grant_id}", response_model=data_use.Grant)
    async def revoke_grant(request: Request, grant_id: str):
        return await answer(request, lambda actor: service.revoke_grant(actor, grant_id))

    app.include_router(router)
    return service
