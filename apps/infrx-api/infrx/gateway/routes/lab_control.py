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
capability, and only then reads a body or asks the operations; nothing in a body names an
actor. Aggregate health is L2's (`LabAccess.aggregates`: closed, no customer identity).

`ControlOperations` is the port L3 (`infrx/lab/control`, lab-access-lw2) implements - an
interface request, driven here by a fake. L3 re-checks ownership (another provider's id is
`not_found`), digests and state, and raises the typed refusals `lab_auth.refusal` renders.
Until it is wired the operations answer 503 `unavailable`; aggregates are served regardless.
Mounted only when the composition put a `LabControl` on `rt.lab_control` (LAB_CONTROL, off).

E3L-F4: `reject` is the platform operator's, on the same session door: the session user must
be an operator (`ControlOperations.operator`, `profiles.is_operator`) before the body is read;
no provider membership or `provider_org_id` is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Sequence

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...contracts import errors
from ...contracts.v2.records import ProviderCapability as Cap
from ...contracts.v2.records import ProviderRole
from .. import lab_auth
from . import intake

CONTROL_PREFIX = "/lab/v1/control"
MAX_BODY_BYTES = 16_384
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


class Actor(Record):
    """Always the session's current membership, never a request value."""

    provider_org_id: str
    user_id: str
    role: ProviderRole


class ControlOperations(Protocol):
    """L3's operations (interface request WR-LAB-API-1). Refusals are typed `DomainError`s:
    `NotFound` (not this provider's), `Forbidden`, `InvalidRequest`, `Conflict`,
    `DependencyUnavailable`. A publish or rollback is a proposal; an operator decides it."""

    async def models(self, actor: Actor) -> Sequence[Model]: ...
    async def deployments(self, actor: Actor) -> Sequence[Deployment]: ...
    async def proposals(self, actor: Actor) -> Sequence[Proposal]: ...
    async def register(self, actor: Actor, registration: Registration) -> Deployment:
        """A model revision and its private dev deployment revision."""
    async def smoke(self, actor: Actor, deployment_revision_id: str) -> Deployment: ...
    async def propose(self, actor: Actor, kind: str,
                      deployment_revision_id: str) -> Proposal: ...
    async def operator(self, user_id: str):
        """The user's operator session (`Forbidden` for anyone else)."""
    async def reject(self, operator, proposal_id: str, reason: str) -> Proposal: ...


@dataclass(frozen=True)
class LabControl:
    sessions: lab_auth.Sessions
    access: object                               # infrx.lab.access.LabAccess
    operations: ControlOperations | None = None  # None until L3 is wired: 503


def _dump(records) -> list:
    return [record.model_dump(mode="json") for record in records]


def register(app, rt, control: LabControl | None = None):
    """Mount the control routes over `control` (default `rt.lab_control`); without one
    nothing is mounted and `None` is returned."""
    control = control if control is not None else getattr(rt, "lab_control", None)
    if control is None:
        return None
    limits = rt.settings.pilot

    async def actor(request: Request, capability: Cap) -> Actor:
        user_id = await lab_auth.authenticate(request, control.sessions)
        membership = await lab_auth.member(
            control.access, user_id, request.query_params.get("provider_org_id", ""), capability)
        return Actor(provider_org_id=membership.provider_org_id, user_id=user_id,
                     role=membership.role)

    def operations() -> ControlOperations:
        if control.operations is None:      # expected before L3 merges: a 503, not a bug
            raise errors.DependencyUnavailable("L3's control operations are not wired")
        return control.operations

    async def body(request: Request, model: type[Record]) -> Record:
        intake.check_content_type(request)
        raw = await intake.read_body(request, max_bytes=MAX_BODY_BYTES,
                                     timeout_s=limits.intake_timeout_s, clock=rt.clock)
        try:
            return model.model_validate(intake.parse_object(intake.decode_utf8(raw)))
        except ValidationError:
            raise errors.InvalidRequest("invalid body") from None

    def listing(name: str):
        @lab_auth.guarded
        async def read(request: Request):
            who = await actor(request, Cap.read_aggregate_health)
            return lab_auth.ok({"data": _dump(await getattr(operations(), name)(who))})
        return read

    for name in ("models", "deployments", "proposals"):
        app.add_api_route(f"{CONTROL_PREFIX}/{name}", listing(name), methods=["GET"])

    @app.get(f"{CONTROL_PREFIX}/aggregates")
    @lab_auth.guarded
    async def aggregates(request: Request):
        who = await actor(request, Cap.read_aggregate_health)
        rows = await control.access.aggregates(who.user_id, who.provider_org_id)
        return lab_auth.ok({"data": _dump(rows)})

    @app.post(f"{CONTROL_PREFIX}/register")
    @lab_auth.guarded
    async def register_model(request: Request):
        who = await actor(request, Cap.manage_dev_deployment)
        registration = await body(request, Registration)
        created = await operations().register(who, registration)
        return lab_auth.ok(created.model_dump(mode="json"), 201)

    @app.post(CONTROL_PREFIX + "/deployments/{deployment_revision_id}/smoke")
    @lab_auth.guarded
    async def smoke(request: Request):
        who = await actor(request, Cap.manage_dev_deployment)
        tested = await operations().smoke(who, request.path_params["deployment_revision_id"])
        return lab_auth.ok(tested.model_dump(mode="json"))

    @app.post(f"{CONTROL_PREFIX}/proposals")
    @lab_auth.guarded
    async def propose(request: Request):
        who = await actor(request, Cap.propose_publication)
        wanted = await body(request, ProposalRequest)
        proposal = await operations().propose(who, wanted.kind, wanted.deployment_revision_id)
        return lab_auth.ok(proposal.model_dump(mode="json"), 201)

    @app.post(CONTROL_PREFIX + "/proposals/{proposal_id}/reject")
    @lab_auth.guarded
    async def reject(request: Request):
        operator = await operations().operator(
            await lab_auth.authenticate(request, control.sessions))
        decision = await body(request, Rejection)
        rejected = await operations().reject(operator, request.path_params["proposal_id"],
                                             decision.reason)
        return lab_auth.ok(rejected.model_dump(mode="json"))

    return control
