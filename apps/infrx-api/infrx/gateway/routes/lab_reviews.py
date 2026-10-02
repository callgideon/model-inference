"""AP-08 08e: request review over HTTP (contracts.md §3 "Request review").

    GET  /lab/v1/traces/{request_id}/feedback   the customer's shared signals (0038's door)
                                                beside the provider's human reviews (0064)
    POST /lab/v1/traces/{request_id}/reviews    one human review (201), keyed by
                                                `Idempotency-Key`; provenance `human` and the
                                                reviewer are the server's

Replaces the Lab's direct `lab_review_feedback` RPC (`apps/lab/lib/services/review`). Same
actor rule, envelope and mounting switch as `lab_judge` (`rt.lab_judge`).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Query, Request

from ...lab.judge_api import service as s
from .. import control
from ..control import R270Route
from .lab_judge import KEY, session_user

PREFIX = "/lab/v1/traces"


def register(app: FastAPI, rt: Any) -> None:
    judge: s.JudgeApi | None = getattr(rt, "lab_judge", None)
    if judge is None:
        return
    r = APIRouter(prefix=PREFIX, route_class=R270Route)

    @r.get("/{request_id}/feedback", response_model=s.FeedbackDoc)
    async def feedback(request: Request, request_id: str, provider_org_id: str = Query()):
        user = await session_user(rt, request)
        return control.ok(await judge.feedback(user, provider_org_id, request_id))

    @r.post("/{request_id}/reviews", status_code=201, response_model=s.ReviewDoc)
    async def review(request: Request, request_id: str, body: s.ReviewBody,
                     provider_org_id: str = Query(), idempotency_key: str = KEY):
        user = await session_user(rt, request)
        return control.ok(await judge.review(user, provider_org_id, request_id,
                                             idempotency_key, body), 201)

    app.include_router(r)
