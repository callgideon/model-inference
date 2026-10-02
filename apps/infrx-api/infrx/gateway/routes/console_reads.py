"""AP-02: the console's read routes (R270) over `infrx.console.reads.ConsoleReads`.

`GET /console/v1/credits | credit-ledger | legacy-statement | requests | requests/{id} |
requests/{id}/result | keys | account/members` for the signed-in individual, and the operator
projections `GET /operator/v1/accounts | wallet-drift | unknown-usage | audit` - reads only.
The actor comes from `rt.actors` (never the request); every answer is no-store; every failure is
the R270 envelope (`control.error_response`). Mounted only when the composition sets
`rt.console_reads` (default OFF): with it None this registers nothing.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from infrx.console import reads as r
from infrx.contracts import api
from infrx.gateway import control

#: Documented failures (the OpenAPI export lists the envelope for each).
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": api.ErrorEnvelope} for status in (401, 403, 404, 409, 410, 422, 503)}
LIMIT = Query(None, description=f"page size 1..{api.MAX_PAGE_SIZE} (default "
                                f"{api.DEFAULT_PAGE_SIZE})")
CURSOR = Query(None, description="the next_cursor of the previous page, passed back as given")


def register(app: FastAPI, rt: Any) -> None:
    reads: r.ConsoleReads | None = getattr(rt, "console_reads", None)
    if reads is None:
        return

    async def answer(request: Request, read: Callable[[api.Actor], Awaitable[Any]]) -> JSONResponse:
        rid = control.request_id(request)
        try:
            return control.ok(await read(await rt.actors.actor(request)))
        except Exception as exc:  # noqa: BLE001 - every failure leaves as the sanitized envelope
            return control.error_response(exc, rid)

    @app.get("/console/v1/credits", response_model=r.Credits, responses=ERRORS,
             operation_id="console_credits")
    async def credits(request: Request) -> JSONResponse:
        return await answer(request, reads.credits)

    @app.get("/console/v1/credit-ledger", response_model=api.ListPage[r.LedgerEntry],
             responses=ERRORS, operation_id="console_credit_ledger")
    async def credit_ledger(request: Request, limit: str | None = LIMIT,
                            cursor: str | None = CURSOR) -> JSONResponse:
        return await answer(request, lambda a: reads.credit_ledger(a, limit, cursor))

    @app.get("/console/v1/legacy-statement", response_model=r.LegacyStatement, responses=ERRORS,
             operation_id="console_legacy_statement")
    async def legacy_statement(request: Request) -> JSONResponse:
        return await answer(request, reads.legacy_statement)

    @app.get("/console/v1/requests", response_model=api.ListPage[r.RequestSummary],
             responses=ERRORS, operation_id="console_requests")
    async def requests(request: Request, limit: str | None = LIMIT, cursor: str | None = CURSOR,
                       model: str | None = Query(None, description="requested model or revision"),
                       key_id: str | None = Query(None, description="the key the request used"),
                       from_: str | None = Query(None, alias="from",
                                                 description="ISO-8601 instant, inclusive"),
                       to: str | None = Query(None, description="ISO-8601 instant, exclusive"),
                       ) -> JSONResponse:
        async def read(actor: api.Actor) -> Any:
            filters = r.request_filters(model, key_id, from_, to)
            return await reads.requests(actor, limit, cursor, filters)
        return await answer(request, read)

    @app.get("/console/v1/requests/{request_id}", response_model=r.RequestSummary,
             responses=ERRORS, operation_id="console_request")
    async def one_request(request: Request, request_id: str) -> JSONResponse:
        return await answer(request, lambda a: reads.request(a, request_id))

    @app.get("/console/v1/requests/{request_id}/result", response_model=r.RequestResult,
             responses=ERRORS, operation_id="console_request_result")
    async def result(request: Request, request_id: str) -> JSONResponse:
        return await answer(request, lambda a: reads.result(a, request_id))
