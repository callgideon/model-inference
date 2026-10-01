"""AP-03 (wave 7): the operator console's mutations as R270 routes.

    POST /operator/v1/credit-adjustments  201 CreditAdjusted (200 on a replay)
    POST /operator/v1/suspensions         200 SuspensionSet
    POST /operator/v1/key-revocations     200 KeyRevoked

Each wraps the audited 0025 function (`operator_adjust_credit`, `operator_set_suspension`,
`operator_revoke_key`) with a mandatory reason and Idempotency-Key. A non-operator is
refused here, again in the repository and again by the SQL guard. Mounted only beside the
console routes (`rt.console_actions`; default OFF).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from ...console import actions as acts
from ...contracts import api, errors
from .. import control
from .console_actions import IDEMPOTENCY, ControlRoute, actor_of


def register(app, rt):
    repo = getattr(rt, "console_actions", None)
    if repo is None:
        return None
    router = APIRouter(route_class=ControlRoute)
    resolve = actor_of(rt)

    async def operator(actor: api.Actor = Depends(resolve)) -> api.Actor:
        if not actor.operator:
            raise errors.Forbidden("operator authority is required")
        return actor

    who = Depends(operator)

    @router.post("/operator/v1/credit-adjustments", response_model=acts.CreditAdjusted,
                 status_code=201)
    async def adjust(body: acts.CreditAdjustment, actor: api.Actor = who,
                     idempotency_key: str = Header(alias=IDEMPOTENCY)):
        done = await repo.adjust_credit(actor, str(body.user_id), body.amount, body.reason,
                                        idempotency_key)
        return control.ok(done, 200 if done.replayed else 201)

    @router.post("/operator/v1/suspensions", response_model=acts.SuspensionSet)
    async def suspend(body: acts.Suspension, actor: api.Actor = who,
                      idempotency_key: str = Header(alias=IDEMPOTENCY)):
        return control.ok(await repo.set_suspension(actor, str(body.org_id), body.suspended,
                                                    body.reason, idempotency_key))

    @router.post("/operator/v1/key-revocations", response_model=acts.KeyRevoked)
    async def revoke(body: acts.KeyRevocation, actor: api.Actor = who,
                     idempotency_key: str = Header(alias=IDEMPOTENCY)):
        return control.ok(await repo.revoke_key_as_operator(actor, str(body.key_id),
                                                            body.reason, idempotency_key))

    app.include_router(router)
    return repo
