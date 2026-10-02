"""AP-01 01b: `GET /console/v1/me` and `GET /console/v1/capabilities`, the App shell's account.

The account is the verified session's own (`rt.actors`, `SessionActors`), read from the database
on every call (`rt.identity`): verified, onboarding, suspended, operator and the signup grant are
the server's, never a client's guess (the App's `resolveConsumerContext` states). Feature
availability is the deployment's switches (R270 `Availability`), allowed actions the account's.
Mounted only when the composition root provides `rt.actors` and `rt.identity`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request

from ...console import EnvelopeRoute
from ...console.session import Account, web_session
from ...contracts import api, errors
from .. import control

#: console feature -> the `DeploymentSettings` switch that mounts it.
FEATURES = (("feedback", "feedback_api"), ("trace_export", "trace_export_api"))


class GrantStatus(api.Wire):
    state: Literal["granted", "not_granted"]
    amount: api.Money | None = None
    granted_at: datetime | None = None


class Me(api.Wire):
    actor: api.Actor
    state: Literal["unverified", "onboarding", "ready"]
    suspended: bool
    signup_grant: GrantStatus


class ConsoleCapabilities(api.Wire):
    actions: dict[str, bool]
    features: dict[str, api.Availability]


def switched(deployment, table: tuple[tuple[str, str], ...]) -> dict[str, api.Availability]:
    """Each feature as its switch says, at this instant."""
    at = datetime.now(UTC).isoformat()
    return {name: api.Availability(state="configured", verified_at=at)
            if getattr(deployment, switch) else
            api.Availability(state="disabled", reason="switch_off", verified_at=at)
            for name, switch in table}


def state_of(account: Account) -> Literal["unverified", "onboarding", "ready"]:
    if not account.verified:
        return "unverified"
    return "ready" if account.wallet else "onboarding"


async def signed_in(rt, request: Request) -> tuple[api.Actor, Account]:
    """The session's actor and its account.
    ponytail: the actor source read the account too; pass it on `request.state` if the
    second read ever matters."""
    actor = web_session(await rt.actors.actor(request))
    account = await rt.identity.account(actor.user_id)
    if account is None:
        raise errors.InvalidApiKey("the session's user has no account")
    return actor, account


def register(app, rt) -> None:
    if getattr(rt, "actors", None) is None or getattr(rt, "identity", None) is None:
        return
    router = APIRouter(route_class=EnvelopeRoute)

    @router.get("/console/v1/me", response_model=Me, operation_id="console_me")
    async def me(request: Request):
        actor, account = await signed_in(rt, request)
        grant = GrantStatus(state="not_granted") if account.grant_amount is None else \
            GrantStatus(state="granted", granted_at=account.granted_at,
                        amount=api.Money(amount=account.grant_amount, unit="CREDIT"))
        return control.ok(Me(actor=actor, state=state_of(account), suspended=account.suspended,
                             signup_grant=grant))

    @router.get("/console/v1/capabilities", response_model=ConsoleCapabilities,
                operation_id="console_capabilities")
    async def capabilities(request: Request):
        _, account = await signed_in(rt, request)
        ready = state_of(account) == "ready"
        actions = {"create_key": ready and not account.suspended,
                   "revoke_key": account.wallet,
                   "claim_signup_grant": account.verified and account.grant_amount is None,
                   "operator_console": account.operator}
        features = {**switched(rt.settings.deployment, FEATURES),
                    "dedicated_endpoints": api.Availability(state="disabled",
                                                            reason="not_offered")}
        return control.ok(ConsoleCapabilities(actions=actions, features=features))

    app.include_router(router)
