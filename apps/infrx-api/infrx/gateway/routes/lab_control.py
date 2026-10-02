"""WR-L4-1: `/lab/v1/control`, the Lab's control surface over L3's operations.

    GET  /lab/v1/control/{models|deployments|proposals|aggregates}?provider_org_id=
    POST /lab/v1/control/register?provider_org_id=            {name, artifact_digest,
                                                                schema_version, runtime}
    POST /lab/v1/control/deployments/{id}/smoke?provider_org_id=
    POST /lab/v1/control/proposals?provider_org_id=           {kind, deployment_revision_id}
    POST /lab/v1/control/proposals/{id}/reject                {reason}   (operators only)

Records are the Lab's `apps/lab/lib/services/control/port.ts` in snake_case; lists are
`{"data": [...]}`. Every call re-derives the actor - (provider, user, role) - from the forwarded
session and the user's current `LabAccess` membership (`lab_auth`), checks the operation's
capability, and only then validates a body or asks the operations; nothing in a body names an
actor. Aggregate health is L2's (`LabAccess.aggregates`: closed, no customer identity).

`ControlOperations` is the port L3 (`infrx/lab/control`, lab-access-lw2) implements - an
interface request, driven here by a fake. L3 re-checks ownership (another provider's id is
`not_found`), digests and state, and raises the typed refusals `lab_auth.refusal` renders.
Until it is wired the operations answer 503 `unavailable`; aggregates are served regardless.
Mounted only when the composition put a `LabControl` on `rt.lab_control` (LAB_CONTROL, off).

E3L-F4: `reject` is the platform operator's, on the same session door: the session user must
be an operator (`ControlOperations.operator`, `profiles.is_operator`) before the body is read;
no provider membership or `provider_org_id` is involved.

AP-06 06a (R270, typed; mounted only over `rt.lab_publication`, `operator_publication`):

    POST   /lab/v1/control/endpoints/{id}/keys?provider_org_id=     201 DevKeyIssued
    GET    /lab/v1/control/endpoints/{id}/keys?provider_org_id=     {data, next_cursor}
    DELETE /lab/v1/control/endpoints/{id}/keys/{key_id}?provider_org_id=   200 DevKey
    GET    /lab/v1/control/dev-wallet?provider_org_id=              200 DevWallet

`LabControl.issue_dev_key` on the endpoint's newest validated dev revision: a provider_dev
credential scoped to that endpoint (catalog resolution keeps it off public listings and keeps
consumer keys off the private one). The actor is `rt.actors`' verified session; membership and
capability are `LabAccess`'s, re-read on every call, before any Idempotency-Key is recorded
in the provider's scope. The secret is returned once; a replay names the key without it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Sequence

from fastapi import Depends, FastAPI, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ...contracts import errors
from ...contracts.v2.records import ProviderCapability as Cap
from .. import lab_auth

if TYPE_CHECKING:
    from ...lab.access import LabAccess

CONTROL_PREFIX = "/lab/v1/control"
#: R270: every new control mutation's key (AP-06's routes here and in operator_publication)
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
DIGEST = r"^sha256:[0-9a-f]{64}$"


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Registration(Record):
    name: str = Field(min_length=1, max_length=200)
    artifact_digest: str = Field(pattern=DIGEST)
    schema_version: str = Field(min_length=1, max_length=50)
    runtime: str = Field(min_length=1, max_length=300)


class ProposalRequest(Record):
    kind: Literal["publish", "rollback"]
    deployment_revision_id: str = Field(min_length=1, max_length=64)


class Rejection(Record):
    reason: str = Field(min_length=1, max_length=500)


class Model(Record):
    model_id: str
    revision_label: str
    artifact_digest: str
    schema_version: str
    runtime: str
    registered_at: datetime


class Deployment(Record):
    deployment_revision_id: str
    model_id: str
    serving_version_id: str
    revision_label: str
    runtime: str
    schema_version: str
    rate_card_version: str | None
    environment: Literal["dev", "prod"]
    visibility: Literal["private", "public"]
    state: Literal["active", "retired"]
    smoke: Literal["none", "passed", "failed"]
    created_at: datetime


class Proposal(Record):
    proposal_id: str
    kind: Literal["publish", "rollback"]
    deployment_revision_id: str
    state: Literal["proposed", "approved", "rejected"]
    proposed_at: datetime
    decided_at: datetime | None


Actor = lab_auth.Actor                           # L3 (`infrx/lab/control`) imports it from here


class ControlOperations(Protocol):
    """L3's operations (interface request WR-LAB-API-1). Refusals are typed `DomainError`s:
    `NotFound` (not this provider's), `Forbidden`, `InvalidRequest`, `Conflict`,
    `DependencyUnavailable`. A publish or rollback is a proposal; an operator decides it."""

    async def models(self, actor: Actor) -> Sequence[Model]: ...
    async def deployments(self, actor: Actor) -> Sequence[Deployment]: ...
    async def proposals(self, actor: Actor) -> Sequence[Proposal]: ...
    async def register(self, actor: Actor, registration: Registration) -> Deployment:
        """A model revision and its private dev deployment revision."""
        ...
    async def smoke(self, actor: Actor, deployment_revision_id: str) -> Deployment: ...
    async def propose(self, actor: Actor, kind: str,
                      deployment_revision_id: str) -> Proposal: ...
    async def operator(self, user_id: str) -> Any:
        """The user's operator session (`Forbidden` for anyone else)."""
        ...
    async def reject(self, operator, proposal_id: str, reason: str) -> Proposal: ...


@dataclass(frozen=True)
class LabControl:
    sessions: lab_auth.Sessions
    access: LabAccess
    operations: ControlOperations | None = None  # None until L3 is wired: 503


class Rows[T: BaseModel](Record):
    """A control listing: `{"data": [...]}` (no cursor: the lists are a provider's own few)."""

    data: tuple[T, ...]


def _dump(records: Sequence[BaseModel]) -> list[dict[str, Any]]:
    return [record.model_dump(mode="json") for record in records]


def register(app: FastAPI, rt: Any, control: LabControl | None = None) -> LabControl | None:
    """Mount the control routes over `control` (default `rt.lab_control`); without one
    nothing is mounted and `None` is returned. AP-00 (R270): the handlers are typed - bodies,
    path parameters and `response_model`s are FastAPI parameters the OpenAPI export
    documents - behind `lab_auth.refusal_route`, which keeps the `{refusal}` wire: the
    identity is a dependency, so it answers before any body is validated."""
    _dev_routes(app, rt)
    control = control if control is not None else getattr(rt, "lab_control", None)
    if control is None:
        return None
    from ...lab.access import DeploymentAggregate

    def actor(capability: Cap) -> Any:
        async def who(request: Request, provider_org_id: str = Query("")) -> Actor:
            return await lab_auth.lab_actor(request, control.sessions, control.access, capability)
        return Depends(who)

    def operations() -> ControlOperations:
        if control.operations is None:      # expected before L3 merges: a 503, not a bug
            raise errors.DependencyUnavailable("L3's control operations are not wired")
        return control.operations

    async def operator(request: Request) -> Any:
        return await operations().operator(
            await lab_auth.authenticate(request, control.sessions))

    refused = lab_auth.refusal_route(rt)

    def route(method: str, path: str, endpoint: Any, response_model: Any,
              status_code: int = 200) -> None:
        """On the app's own table (not `include_router`), as every router here mounts."""
        app.router.add_api_route(CONTROL_PREFIX + path, endpoint, methods=[method],
                          response_model=response_model, status_code=status_code,
                          responses=lab_auth.REFUSED, route_class_override=refused)

    def listing(name: str):
        async def read(who: Actor = actor(Cap.read_aggregate_health)):
            return lab_auth.ok({"data": _dump(await getattr(operations(), name)(who))})
        return read

    for name, record in (("models", Model), ("deployments", Deployment), ("proposals", Proposal)):
        route("GET", f"/{name}", listing(name), Rows[record])

    async def aggregates(who: Actor = actor(Cap.read_aggregate_health)):
        rows = await control.access.aggregates(who.user_id, who.provider_org_id)
        return lab_auth.ok({"data": _dump(rows)})

    async def register_model(registration: Registration,
                             who: Actor = actor(Cap.manage_dev_deployment)):
        created = await operations().register(who, registration)
        return lab_auth.ok(created.model_dump(mode="json"), 201)

    async def smoke(deployment_revision_id: str, who: Actor = actor(Cap.manage_dev_deployment)):
        tested = await operations().smoke(who, deployment_revision_id)
        return lab_auth.ok(tested.model_dump(mode="json"))

    async def propose(wanted: ProposalRequest, who: Actor = actor(Cap.propose_publication)):
        proposal = await operations().propose(who, wanted.kind, wanted.deployment_revision_id)
        return lab_auth.ok(proposal.model_dump(mode="json"), 201)

    async def reject(proposal_id: str, decision: Rejection, decider: Any = Depends(operator)):
        rejected = await operations().reject(decider, proposal_id, decision.reason)
        return lab_auth.ok(rejected.model_dump(mode="json"))

    route("GET", "/aggregates", aggregates, Rows[DeploymentAggregate])
    route("POST", "/register", register_model, Deployment, 201)
    route("POST", "/deployments/{deployment_revision_id}/smoke", smoke, Deployment)
    route("POST", "/proposals", propose, Proposal, 201)
    route("POST", "/proposals/{proposal_id}/reject", reject, Proposal)
    return control


class DevKeyName(Record):
    name: str = Field(default="dev", min_length=1, max_length=200)


class DevKeyIssued(Record):
    """`secret` only on the call that issued it; a replay carries null (lost: revoke and
    issue another - never a re-reveal)."""

    key_id: str
    endpoint_id: str
    prefix: str
    secret: str | None
    secret_returned: bool
    replayed: bool


def _dev_routes(app: FastAPI, rt: Any) -> None:
    """AP-06 06a over `rt.lab_publication` (nothing without one)."""
    pub = getattr(rt, "lab_publication", None)
    if pub is None:
        return
    from fastapi import APIRouter

    from ...contracts import api
    from ...contracts.v2.records import DeploymentState, Environment
    from .. import control as r270
    from .operator_publication import ERRORS, DevKey, DevWallet, once

    domain = pub.operations.control
    router = APIRouter(route_class=r270.R270Route, responses=ERRORS)
    base = CONTROL_PREFIX + "/endpoints/{endpoint_id}/keys"

    async def member(request: Request, provider_org_id: str, capability: Cap) -> api.Actor:
        actor = await rt.actors.actor(request)
        if actor.audience != "session" or not actor.user_id:
            raise errors.InvalidApiKey("an API key is not a Lab session")
        await domain.access.require(actor.user_id, provider_org_id, capability)
        return actor.model_copy(update={"provider_org_id": provider_org_id})

    def credentials():
        if pub.credentials is None:
            raise errors.DependencyUnavailable("dev credential reads are not composed")
        return pub.credentials

    @router.post(base, response_model=DevKeyIssued, status_code=201, operation_id="issueDevKey")
    async def issue(request: Request, endpoint_id: str, body: DevKeyName, key: IdempotencyKey,
                    provider_org_id: str = Query()):
        actor = await member(request, provider_org_id, Cap.manage_dev_deployment)
        ready = [d for d in await pub.operations.reads.provider_deployments(provider_org_id)
                 if d.endpoint_id == endpoint_id and d.environment is Environment.dev
                 and d.state is DeploymentState.ready_private]
        if not ready:
            raise errors.NotFound("no validated dev revision on this endpoint")
        revision = max(ready, key=lambda d: d.created_at).deployment_revision_id

        async def work(op):
            issued = await domain.issue_dev_key(actor.user_id, provider_org_id, revision,
                                                body.name)
            await pub.ops.advance(op.operation_id, op.fence,
                                  f"issued:{issued.key_id}:{issued.prefix}")
            return DevKeyIssued(key_id=issued.key_id, endpoint_id=endpoint_id,
                                prefix=issued.prefix, secret=issued.secret,
                                secret_returned=True, replayed=False)

        async def done(op):
            kind, _, rest = (op.phase or "").partition(":")
            key_id, _, prefix = rest.partition(":")
            return None if kind != "issued" else DevKeyIssued(
                key_id=key_id, endpoint_id=endpoint_id, prefix=prefix, secret=None,
                secret_returned=False, replayed=True)

        issued, _ = await once(pub.ops, actor, "dev_key.issue", key,
                               {"endpoint_id": endpoint_id, "name": body.name}, work, done,
                               r270.request_id(request))
        return r270.ok(issued, 200 if issued.replayed else 201)

    @router.get(base, response_model=api.ListPage[DevKey], operation_id="listDevKeys")
    async def keys(request: Request, endpoint_id: str, provider_org_id: str = Query()):
        await member(request, provider_org_id, Cap.manage_dev_deployment)
        return r270.ok(api.ListPage[DevKey](
            data=tuple(await credentials().keys(provider_org_id, endpoint_id))))

    @router.delete(base + "/{key_id}", response_model=DevKey, operation_id="revokeDevKey")
    async def revoke(request: Request, endpoint_id: str, key_id: str,
                     provider_org_id: str = Query(),
                     key: str | None = Header(None, alias="Idempotency-Key", max_length=200)):
        actor = await member(request, provider_org_id, Cap.manage_dev_deployment)
        return r270.ok(await credentials().revoke(provider_org_id, endpoint_id, key_id,
                                                  actor=f"lab:{actor.user_id}",
                                                  idempotency_key=key))

    @router.get(CONTROL_PREFIX + "/dev-wallet", response_model=DevWallet,
                operation_id="getDevWallet")
    async def wallet(request: Request, provider_org_id: str = Query()):
        await member(request, provider_org_id, Cap.manage_dev_deployment)
        return r270.ok(await credentials().wallet(provider_org_id))

    app.include_router(router)
