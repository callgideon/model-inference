"""R270 (wave 7): the HTTP rendering shared by every NEW control route, and the actor seam.

A new route module is `register(app, rt)` like the others (`infrx.gateway.app.ROUTERS`
mounts it; the coordinator adds the line). Its handlers declare pydantic bodies and
`response_model`s as FastAPI parameters so AP-00's OpenAPI export documents them, and
render every failure through `error_response` (never a bare 500, never a traceback).

The actor seam: a route never reads identity from the body. It asks `rt.actors`, an
`ActorSource` the composition root provides (AP-01's session resolver in production,
`StaticActors` in tests), and receives an `infrx.contracts.api.Actor` or a domain error.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from infrx.contracts import api, errors

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
