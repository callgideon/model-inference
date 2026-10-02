"""AP-07a: the grantor's data-use routes (R270), over `infrx.console.data_use.DataUse`.

    GET    /console/v1/data-use                -> DataUseDoc (consent head, keys, grants)
    PUT    /console/v1/keys/{key_id}/capture   -> DataUseDoc (a new consent version, or a replay)
    GET    /console/v1/data-grants             -> {data: [Grant], next_cursor: null}
    POST   /console/v1/data-grants             -> 201 Grant (the next version), 200 on a replay
    DELETE /console/v1/data-grants/{grant_id}  -> Grant (revoked; a second DELETE replays)

The actor is `rt.actors`' (a verified session); the organization is the actor's. Mounted only
when the composition put a `DataUse` on `rt.data_use` (off by default). Every failure is the R270
envelope, a body that does not validate included (`EnvelopeRoute`: route-local, so the other
families keep their own 422s). Replays are state-based - the same decision is answered from the
current state without a new version - so no `Idempotency-Key` store is needed here.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from ...console import data_use
from ...contracts import api, errors
from .. import control

log = logging.getLogger("infrx.gateway.console_data_use")

DATA_USE_PATH = "/console/v1/data-use"
KEYS_PATH = "/console/v1/keys"
GRANTS_PATH = "/console/v1/data-grants"


def invalid(exc: RequestValidationError, rid: str) -> JSONResponse:
    """A body or parameter that does not validate: 422 with the fields, never the input."""
    body = api.envelope(errors.InvalidRequest("the request does not validate"), rid)
    fields = tuple(api.FieldError(field=".".join(str(p) for p in e["loc"][1:]) or "body",
                                  code=e["type"], message=e["msg"]) for e in exc.errors())
    body = body.model_copy(update={"error": body.error.model_copy(update={"field_errors": fields})})
    return JSONResponse(body.model_dump(mode="json"), status_code=422, headers=control.NO_STORE)


class EnvelopeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return invalid(exc, control.request_id(request))
        return route


def register(app: FastAPI, rt: Any) -> data_use.DataUse | None:
    service = getattr(rt, "data_use", None)
    if service is None:
        return None
    router = APIRouter(route_class=EnvelopeRoute)

    async def answer(request: Request, work, status: int = 200) -> JSONResponse:
        rid = control.request_id(request)
        try:
            result = await work(await rt.actors.actor(request))
        except Exception as exc:                       # noqa: BLE001 - every failure is an envelope
            if not isinstance(exc, errors.DomainError):
                log.exception("data-use request %s failed", rid)
            return control.error_response(exc, rid)
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
