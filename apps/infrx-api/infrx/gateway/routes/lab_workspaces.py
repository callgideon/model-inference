"""AP-01 01c: the Lab's workspaces, capabilities and members over the verified session.

- `GET /lab/v1/workspaces`: the session user's current provider memberships, each with its
  role's capability set (`contracts.v2.records.ROLE_CAPABILITIES`, the source of truth the Lab's
  `lib/auth/access.ts` table copies today and will consume from here, AP-09);
- `GET /lab/v1/capabilities?provider_org_id=`: one membership's role, capabilities and the Lab
  features' availability; a provider the user is not a current member of is a 404;
- `GET /lab/v1/workspaces/{id}/members`: any current member reads the current members;
- `POST /lab/v1/workspaces/{id}/members` / `DELETE .../members/{user}`: an administrator
  (`manage_members`) grants or revokes - 0007's rules: one current row per pair, a role change
  is a revocation plus a new grant, never an update. No one changes their own membership, so
  nobody self-promotes and an administrator cannot lock themself out by accident.

Membership is read current on every call (`LabAccess`, the store clock): a revocation refuses
the very next request. Mounted when the composition root provides `rt.actors`, `rt.identity`
and `rt.lab_access`.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from ...console import EnvelopeRoute, idempotency_key
from ...console.session import Member, web_session
from ...contracts import api, errors
from ...contracts.v2.records import ROLE_CAPABILITIES, ProviderCapability, ProviderRole
from .. import control
from .console_me import switched

FEATURES = (("control", "lab_control"), ("traces", "lab_traces"), ("evaluations", "lab_evals"),
            ("pipelines", "lab_pipelines"), ("releases", "lab_releases"),
            ("datasets", "lab_datasets"), ("checkpoints", "lab_checkpoints"))


class Workspace(api.Wire):
    provider_org_id: str
    provider_name: str
    role: str
    capabilities: tuple[str, ...]


class LabCapabilities(api.Wire):
    provider_org_id: str
    role: str
    capabilities: tuple[str, ...]
    features: dict[str, api.Availability]


class MemberGrant(api.Wire):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "developer", "administrator"]


def capabilities(role: str) -> tuple[str, ...]:
    return tuple(sorted(c.value for c in ROLE_CAPABILITIES[ProviderRole(role)]))


def register(app, rt) -> None:
    if any(getattr(rt, name, None) is None for name in ("actors", "identity", "lab_access")):
        return
    access, identity = rt.lab_access, rt.identity
    router = APIRouter(route_class=EnvelopeRoute)

    async def workspaces(request: Request) -> tuple[api.Actor, tuple[Workspace, ...]]:
        actor = web_session(await rt.actors.actor(request))
        return actor, tuple(Workspace(
            provider_org_id=w.membership.provider_org_id, provider_name=w.provider_name,
            role=w.membership.role.value, capabilities=capabilities(w.membership.role))
            for w in await access.workspaces(actor.user_id))

    async def administrator(request: Request, provider_org_id: str) -> api.Actor:
        actor = web_session(await rt.actors.actor(request))
        idempotency_key(request)
        await access.require(actor.user_id, provider_org_id, ProviderCapability.manage_members)
        return actor

    @router.get("/lab/v1/workspaces", response_model=api.ListPage[Workspace],
                operation_id="lab_workspaces")
    async def list_workspaces(request: Request):
        _, found = await workspaces(request)
        return control.ok(api.ListPage[Workspace](data=found))

    @router.get("/lab/v1/capabilities", response_model=LabCapabilities,
                operation_id="lab_capabilities")
    async def lab_capabilities(request: Request, provider_org_id: str = ""):
        _, found = await workspaces(request)
        workspace = next((w for w in found if w.provider_org_id == provider_org_id), None)
        if workspace is None:
            raise errors.NotFound("no such provider workspace")
        return control.ok(LabCapabilities(
            provider_org_id=workspace.provider_org_id, role=workspace.role,
            capabilities=workspace.capabilities,
            features=switched(rt.settings.deployment, FEATURES)))

    @router.get("/lab/v1/workspaces/{provider_org_id}/members",
                response_model=api.ListPage[Member], operation_id="lab_members")
    async def list_members(provider_org_id: str, request: Request):
        actor = web_session(await rt.actors.actor(request))
        await access.require(actor.user_id, provider_org_id,
                             ProviderCapability.read_aggregate_health)
        return control.ok(api.ListPage[Member](data=tuple(
            await identity.members(provider_org_id))))

    @router.post("/lab/v1/workspaces/{provider_org_id}/members", response_model=Member,
                 status_code=201, operation_id="lab_member_grant")
    async def grant(provider_org_id: str, body: MemberGrant, request: Request):
        actor = await administrator(request, provider_org_id)
        user_id = await identity.user_by_email(body.email)
        if user_id is None:
            raise errors.NotFound("no account with that email")
        if user_id == actor.user_id:
            raise errors.Forbidden("a member never changes their own membership")
        member, created = await identity.add_member(provider_org_id, user_id, body.role,
                                                    f"session:{actor.user_id}")
        return control.ok(member, 201 if created else 200)

    @router.delete("/lab/v1/workspaces/{provider_org_id}/members/{user_id}",
                   response_model=Member, operation_id="lab_member_revoke")
    async def revoke(provider_org_id: str, user_id: uuid.UUID, request: Request):
        actor = await administrator(request, provider_org_id)
        if str(user_id) == actor.user_id:
            raise errors.Forbidden("a member never changes their own membership")
        return control.ok(await identity.revoke_member(provider_org_id, str(user_id)))

    app.include_router(router)
