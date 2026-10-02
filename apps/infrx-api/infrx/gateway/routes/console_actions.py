"""AP-03 (wave 7): the console's mutations as R270 routes over `infrx.console.actions`.

    POST   /console/v1/keys                          201 KeyCreated (200 on a replay)
    DELETE /console/v1/keys/{key_id}                 200 ApiKey
    POST   /console/v1/signup-grant/claim            200 GrantClaim
    POST   /console/v1/requests/{request_id}/feedback  201 FeedbackAccepted; 503 when off

Mounted only when the composition put a repository on `rt.console_actions` (default OFF).
The actor is a dependency, so it is established before the body is validated; every
failure - a refusal, an invalid body, a bug - leaves as `control.error_response`.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from ...console import actions as acts
from ...contracts import api, errors, wire
from .. import control

log = logging.getLogger(__name__)
IDEMPOTENCY = "Idempotency-Key"


class ControlRoute(APIRoute):
    """R270 rendering for a route: FastAPI's own 422 `{detail}` body and an escaped error
    both become the envelope (no-store, the request id). ponytail: AP-03's two modules use
    it; AP-00 can lift it into `gateway/control.py` for every control family."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def run(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                failed: Exception = errors.InvalidRequest(f"{len(exc.errors())} invalid fields")
            except Exception as exc:
                if not isinstance(exc, errors.DomainError):
                    log.error("%s: %s", request.url.path, type(exc).__name__)
                failed = exc
            return control.error_response(failed, control.request_id(request))
        return run


def actor_of(rt):
    async def actor(request: Request) -> api.Actor:
        return await rt.actors.actor(request)
    return actor


def register(app, rt):
    """Mount the console routes over `rt.console_actions`; nothing without one."""
    repo = getattr(rt, "console_actions", None)
    if repo is None:
        return None
    router = APIRouter(route_class=ControlRoute)
    actor = Depends(actor_of(rt))

    @router.post("/console/v1/keys", response_model=acts.KeyCreated, status_code=201)
    async def create_key(body: acts.KeyCreate, who: api.Actor = actor,
                         idempotency_key: str = Header(alias=IDEMPOTENCY)):
        created = await repo.create_key(who, body.name, idempotency_key)
        return control.ok(created, 200 if created.replayed else 201)

    @router.delete("/console/v1/keys/{key_id}", response_model=acts.ApiKey)
    async def revoke_key(key_id: uuid.UUID, who: api.Actor = actor,
                         idempotency_key: str | None = Header(None, alias=IDEMPOTENCY)):
        return control.ok(await repo.revoke_key(who, str(key_id), idempotency_key))

    @router.post("/console/v1/signup-grant/claim", response_model=acts.GrantClaim)
    async def claim_grant(request: Request, who: api.Actor = actor,
                          idempotency_key: str | None = Header(None, alias=IDEMPOTENCY)):
        """No body: the individual is the session's. An Idempotency-Key is accepted and not
        needed - the grant is keyed by the individual (R71), so every retry replays."""
        if await request.body():
            raise errors.InvalidRequest("the claim takes no body")
        return control.ok(await repo.claim_grant(who))

    @router.post("/console/v1/requests/{request_id}/feedback",
                 response_model=wire.FeedbackAccepted, status_code=201)
    async def submit_feedback(request_id: uuid.UUID, body: acts.FeedbackCreate,
                              who: api.Actor = actor,
                              idempotency_key: str = Header(alias=IDEMPOTENCY)):
        accepted = await acts.submit_feedback(getattr(rt, "feedback", None), who,
                                              str(request_id), body, idempotency_key)
        return control.ok(accepted, 201)

    app.include_router(router)
    return repo
