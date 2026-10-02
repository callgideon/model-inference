"""AP-01 01d: `POST /operator/v1/providers`, the operator's provider onboarding.

The operator is a session whose profile carries `is_operator`, or an operator-audience infrx key
(`auth.context`, through `SessionActors`' key door); every other caller is 403. The provider is
created once (0007's unique slug): a retry answers the same provider, the same slug under
another name is 409, and nothing existing is renamed or reassigned - an existing provider's
models keep their owner. The optional first administrator is granted by the same rules as a
member grant (`PgIdentity.add_member`). Replaces `infra/lab/rollout/lab-release.sh members`'
direct SQL for the API path. Mounted when `rt.actors` and `rt.identity` exist.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import Field

from ...console import EnvelopeRoute, idempotency_key
from ...console.session import Member, Provider
from ...contracts import api, errors
from .. import control


class ProviderRequest(api.Wire):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    display_name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    administrator_email: str | None = Field(default=None, min_length=3, max_length=320)


class ProviderCreated(api.Wire):
    provider: Provider
    administrator: Member | None = None


def register(app, rt) -> None:
    if getattr(rt, "actors", None) is None or getattr(rt, "identity", None) is None:
        return
    identity = rt.identity
    router = APIRouter(route_class=EnvelopeRoute)

    @router.post("/operator/v1/providers", response_model=ProviderCreated, status_code=201,
                 operation_id="operator_provider_create")
    async def create(body: ProviderRequest, request: Request):
        actor = await rt.actors.actor(request)
        if not actor.operator:
            raise errors.Forbidden("provider onboarding is the operator's")
        idempotency_key(request)
        admin = None
        if body.administrator_email is not None:
            admin = await identity.user_by_email(body.administrator_email)
            if admin is None:
                raise errors.NotFound("no account with that email")
        by = f"operator:{actor.user_id}" if actor.audience == "session" \
            else f"operator-key:{actor.org_id}"
        provider, created = await identity.create_provider(body.slug, body.display_name, by)
        member = None if admin is None else (await identity.add_member(
            provider.provider_org_id, admin, "administrator", by))[0]
        return control.ok(ProviderCreated(provider=provider, administrator=member),
                          201 if created else 200)

    app.router.routes.extend(router.routes)     # the app's own table, as every router (lab_datasets)
