"""AP-08 08a: the Lab judge family `/lab/v1/judge/*` (research/plan/api-lifecycle/contracts.md
§8), R270 wire behaviour, over `infrx.lab.judge_api.JudgeApi` (the existing judge doors run as
the session user).

    GET  models  rubrics  configs  configs/{id}  budgets  runs  runs/{id}  runs/{id}/results
         calibration?config_id=
    POST configs (201)  estimates  runs (202 + Location)  runs/{id}/cancel
    PUT  budgets/{payer_ref}

Every request names its workspace (`?provider_org_id=`); the doors check the session user's
CURRENT membership and role in SQL. The actor comes only from `rt.actors`: a verified session
(`audience == "session"`), never a key, never a body field. Mutations require
`Idempotency-Key`; the run id is derived from it (not a browser uuid). Mounted only when
`rt.lab_judge` is composed (WIRING REQUEST: the coordinator adds it to the Lab control unit).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ...contracts import api, errors
from ...lab.judge_api import rubric
from ...lab.judge_api import service as s
from .. import control

log = logging.getLogger(__name__)
PREFIX = "/lab/v1/judge"
KEY = Header(alias="Idempotency-Key", min_length=1, max_length=s.MAX_KEY_CHARS)
LIMIT = Query(api.DEFAULT_PAGE_SIZE, ge=1, le=api.MAX_PAGE_SIZE)


def invalid(exc: RequestValidationError, rid: str) -> JSONResponse:
    """FastAPI's request validation as R270's 422 (`field_errors` name the field; pydantic's
    message, never the input)."""
    fields = tuple(api.FieldError(field=".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                                  code=e["type"], message=e["msg"][:200])
                   for e in exc.errors()[:20])
    body = api.ErrorEnvelope(error=api.ErrorBody(
        code="invalid_request", message=errors.InvalidRequest().message, request_id=rid,
        retryable=False, field_errors=fields))
    return JSONResponse(body.model_dump(mode="json"), 422, headers=control.NO_STORE)


class R270Route(APIRoute):
    """Every failure of a typed handler - request validation, a domain refusal, a bug - as an
    R270 envelope, while the pydantic parameters still document the route (AP-00's export).
    Proposed for `infrx.gateway.control` (WIRING REQUEST): every new family needs it."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return invalid(exc, control.request_id(request))
            except Exception as exc:          # noqa: BLE001 - every failure is an envelope
                if not isinstance(exc, errors.DomainError):
                    log.error("judge route failed: %s", type(exc).__name__)
                return control.error_response(exc, control.request_id(request))
        return guarded


async def session_user(rt: Any, request: Request) -> str:
    """The verified session's user id (R270 actor seam); a key audience is refused."""
    actor = await rt.actors.actor(request)
    if actor.audience != "session" or not actor.user_id:
        raise errors.Forbidden("the judge API acts for a signed-in Lab session")
    return actor.user_id


def register(app: FastAPI, rt: Any) -> None:
    judge: s.JudgeApi | None = getattr(rt, "lab_judge", None)
    if judge is None:
        return
    r = APIRouter(prefix=PREFIX, route_class=R270Route)

    @r.get("/models", response_model=s.JudgeModels)
    async def models(request: Request, provider_org_id: str = Query()):
        await session_user(rt, request)
        return control.ok(judge.models())

    @r.get("/rubrics", response_model=api.ListPage[rubric.RubricDoc])
    async def rubrics(request: Request, provider_org_id: str = Query()):
        await session_user(rt, request)
        return control.ok(api.ListPage[rubric.RubricDoc](data=rubric.rubrics()))

    @r.get("/configs", response_model=api.ListPage[s.ConfigDoc])
    async def configs(request: Request, provider_org_id: str = Query(),
                      cursor: str | None = None, limit: int = LIMIT):
        user = await session_user(rt, request)
        return control.ok(await judge.configs(user, provider_org_id, cursor, limit))

    @r.post("/configs", status_code=201, response_model=s.ConfigDoc)
    async def configure(request: Request, body: s.ConfigBody, provider_org_id: str = Query(),
                        idempotency_key: str = KEY):
        user = await session_user(rt, request)
        return control.ok(await judge.configure(user, provider_org_id, idempotency_key, body),
                          201)

    @r.get("/configs/{config_id}", response_model=s.ConfigDoc)
    async def config(request: Request, config_id: str, provider_org_id: str = Query()):
        user = await session_user(rt, request)
        return control.ok(await judge.config(user, provider_org_id, config_id))

    @r.get("/budgets", response_model=api.ListPage[s.BudgetDoc])
    async def budgets(request: Request, provider_org_id: str = Query()):
        user = await session_user(rt, request)
        return control.ok(await judge.budgets(user, provider_org_id))

    @r.put("/budgets/{payer_ref}", response_model=s.BudgetDoc)
    async def set_budget(request: Request, payer_ref: str, body: s.BudgetBody,
                         provider_org_id: str = Query(), idempotency_key: str = KEY):
        user = await session_user(rt, request)
        return control.ok(await judge.set_budget(user, provider_org_id, payer_ref,
                                                 idempotency_key, body))

    @r.post("/estimates", response_model=s.EstimateDoc)
    async def estimate(request: Request, body: s.EstimateBody, provider_org_id: str = Query()):
        user = await session_user(rt, request)
        return control.ok(await judge.estimate(user, provider_org_id, body))

    @r.post("/runs", status_code=202, response_model=api.OperationDoc)
    async def request_run(request: Request, body: s.RunBody, provider_org_id: str = Query(),
                          idempotency_key: str = KEY):
        user = await session_user(rt, request)
        run = await judge.request_run(user, provider_org_id, idempotency_key, body)
        return control.accepted(run.operation, f"{PREFIX}/runs/{run.run_id}"
                                               f"?provider_org_id={provider_org_id}")

    @r.get("/runs", response_model=api.ListPage[s.RunDoc])
    async def runs(request: Request, provider_org_id: str = Query(),
                   cursor: str | None = None, limit: int = LIMIT):
        user = await session_user(rt, request)
        return control.ok(await judge.runs(user, provider_org_id, cursor, limit))

    @r.get("/runs/{run_id}", response_model=s.RunDoc)
    async def one_run(request: Request, run_id: str, provider_org_id: str = Query()):
        user = await session_user(rt, request)
        return control.ok(await judge.run(user, provider_org_id, run_id))

    @r.get("/runs/{run_id}/results", response_model=api.ListPage[rubric.SampleResult])
    async def results(request: Request, run_id: str, provider_org_id: str = Query(),
                      cursor: str | None = None, limit: int = LIMIT):
        user = await session_user(rt, request)
        return control.ok(await judge.results(user, provider_org_id, run_id, cursor, limit))

    @r.post("/runs/{run_id}/cancel", response_model=s.RunDoc)
    async def cancel(request: Request, run_id: str, provider_org_id: str = Query(),
                     idempotency_key: str = KEY):
        user = await session_user(rt, request)
        return control.ok(await judge.cancel(user, provider_org_id, run_id, idempotency_key))

    @r.get("/calibration", response_model=s.CalibrationView)
    async def calibration(request: Request, provider_org_id: str = Query(),
                          config_id: str = Query()):
        user = await session_user(rt, request)
        return control.ok(await judge.calibration(user, provider_org_id, config_id))

    app.include_router(r)
