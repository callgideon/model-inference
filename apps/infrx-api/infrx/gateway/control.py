"""R270 (wave 7): the HTTP rendering shared by every NEW control route, and the actor seam.

A new route module is `register(app, rt)` like the others (`infrx.gateway.app.ROUTERS`
mounts it; the coordinator adds the line). Its handlers declare pydantic bodies and
`response_model`s as FastAPI parameters so AP-00's OpenAPI export documents them, and
render every failure through `error_response` (never a bare 500, never a traceback).

The actor seam: a route never reads identity from the body. It asks `rt.actors`, an
`ActorSource` the composition root provides (AP-01's session resolver in production,
`StaticActors` in tests), and receives an `infrx.contracts.api.Actor` or a domain error.

`R270Route` (an `APIRouter(route_class=R270Route)`) renders request validation as R270's 422
(`invalid`) and every other failure through `error_response` (WR-5, from AP-08's lab_judge).
"""
from __future__ import annotations

import logging
import uuid
from typing import Protocol

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from infrx.contracts import api, errors

log = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
REQUEST_ID_HEADER = "X-Request-Id"


class ActorSource(Protocol):
    async def actor(self, request: Request) -> api.Actor: ...


class StaticActors:
    """Tests: one fixed actor, or a domain error raised for every request."""

    def __init__(self, actor: api.Actor | None = None, error: errors.DomainError | None = None):
        self._actor, self._error = actor, error

    async def actor(self, request: Request) -> api.Actor:
        if self._error is not None:
            raise self._error
        if self._actor is None:
            raise errors.InvalidApiKey("no session")
        return self._actor


def request_id(request: Request) -> str:
    """The correlation id echoed in every envelope: the caller's header or a fresh uuid."""
    given = request.headers.get(REQUEST_ID_HEADER, "")
    return given if 0 < len(given) <= 64 and given.isprintable() else str(uuid.uuid4())


def ok(content: object, status_code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    """An authenticated response is never cacheable; a pydantic model is dumped here."""
    body = content.model_dump(mode="json") if isinstance(content, BaseModel) else content
    return JSONResponse(body, status_code=status_code, headers={**NO_STORE, **(headers or {})})


def accepted(operation: api.OperationDoc, location: str) -> JSONResponse:
    """202 + Location for a long operation (contracts.md §2)."""
    return ok(operation, 202, {"Location": location})


def error_response(exc: BaseException, rid: str, **refs: str | None) -> JSONResponse:
    status, _, _ = api.status_of(exc)
    body = api.envelope(exc, rid, **refs).model_dump(mode="json")
    headers = dict(NO_STORE)
    retry_after = exc.retry_after_s if isinstance(exc, errors.RateLimitError) else None
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(body, status_code=status, headers=headers)


def invalid(exc: RequestValidationError, rid: str) -> JSONResponse:
    """FastAPI's request validation as R270's 422 (`field_errors` name the field; pydantic's
    message, never the input)."""
    fields = tuple(api.FieldError(field=".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                                  code=e["type"], message=e["msg"][:200])
                   for e in exc.errors()[:20])
    body = api.ErrorEnvelope(error=api.ErrorBody(
        code="invalid_request", message=errors.InvalidRequest().message, request_id=rid,
        retryable=False, field_errors=fields))
    return JSONResponse(body.model_dump(mode="json"), 422, headers=NO_STORE)


class R270Route(APIRoute):
    """Every failure of a typed handler - request validation, a domain refusal, a bug - as an
    R270 envelope, while the pydantic parameters still document the route (AP-00's export)."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return invalid(exc, request_id(request))
            except Exception as exc:          # noqa: BLE001 - every failure is an envelope
                if not isinstance(exc, errors.DomainError):
                    log.error("control route failed: %s", type(exc).__name__)
                return error_response(exc, request_id(request))
        return guarded
