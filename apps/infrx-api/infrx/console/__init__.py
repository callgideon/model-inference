"""AP-01: the web API's identity half - who is calling (`session.SessionActors`, the
`control.ActorSource` the composition root puts on `rt.actors`), their account and provider
workspaces (`state.identity.PgIdentity`), and the one route class every AP-01 router uses.

`EnvelopeRoute` is how a route with FastAPI-declared bodies keeps R270's envelope: FastAPI's own
422 (`{detail: [...]}`) echoes the submitted values - a password included - so a validation
failure is re-rendered here as `invalid_request` with field names and type codes only, and any
domain error a handler raises is rendered by `control.error_response`. A database error no
adapter typed is 503 `dependency_unavailable` (LDP-F3, as the Lab families answer); a bug is a
500 with its type name; nothing either carries is logged.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from ..auth_facade import AuthRefused
from ..contracts import api, errors
from ..gateway import control

log = logging.getLogger("infrx.console")
FIELD_MESSAGE = "This field is not valid."
#: The `Idempotency-Key` a mutation must carry (R270): printable, bounded.
IDEMPOTENCY_KEY = "Idempotency-Key"


def _store_failure(exc: BaseException) -> bool:
    """A database error no adapter typed (a missing grant, a dropped connection)."""
    from psycopg import Error
    return isinstance(exc, Error)


def render(exc: BaseException, rid: str) -> JSONResponse:
    """The R270 envelope for anything a handler raised."""
    if isinstance(exc, AuthRefused):
        return exc.response(rid)
    fields: tuple[api.FieldError, ...] = ()
    if isinstance(exc, RequestValidationError):
        fields = tuple(api.FieldError(
            field=".".join(str(p) for p in e.get("loc", ())[1:]) or "body",
            code=str(e.get("type", "invalid")), message=FIELD_MESSAGE) for e in exc.errors())
        exc = errors.InvalidRequest("invalid body")
    elif isinstance(exc, errors.DomainError) and exc.param:
        fields = (api.FieldError(field=exc.param, code=exc.code, message=exc.message),)
    elif _store_failure(exc):          # LDP-F3: a store that failed is a 503, never a 500
        log.error("control route store failed: %s", type(exc).__name__)
        exc = errors.DependencyUnavailable("a store failed", retry_after_s=5)
    elif not isinstance(exc, errors.DomainError):
        log.error("control route failed: %s", type(exc).__name__)
    if not fields:
        return control.error_response(exc, rid)
    status, _, _ = api.status_of(exc)
    envelope = api.envelope(exc, rid)
    envelope = api.ErrorEnvelope(error=envelope.error.model_copy(update={"field_errors": fields}))
    return JSONResponse(envelope.model_dump(mode="json"), status, headers=dict(control.NO_STORE))


class EnvelopeRoute(APIRoute):
    """`APIRouter(route_class=EnvelopeRoute)`: every failure leaves as the R270 envelope."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except Exception as exc:              # noqa: BLE001 - rendered, never re-raised
                return render(exc, control.request_id(request))
        return route


def idempotency_key(request: Request) -> str:
    """The mutation's `Idempotency-Key`: required, printable, at most 255 characters."""
    key = request.headers.get(IDEMPOTENCY_KEY, "")
    if not 0 < len(key) <= 255 or not key.isprintable():
        raise errors.InvalidRequest("a mutation carries an Idempotency-Key",
                                    param=IDEMPOTENCY_KEY)
    return key
