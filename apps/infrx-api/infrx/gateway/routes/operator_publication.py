"""AP-06 06b/06d (R270): the operator's publication decisions over L3's existing operations.

    GET  /operator/v1/publication-proposals?provider_org_id=   {data, next_cursor}
    GET  /operator/v1/publication-proposals/{id}               the candidate, its readiness and
                                                               the alias's current listing
    POST /operator/v1/publication-proposals/{id}/approve       200 the new listing
    POST /operator/v1/publication-proposals/{id}/reject        200 the decided proposal
    POST /operator/v1/listings/{model_id:path}/rollback        200 the new listing
    POST /operator/v1/dev-wallet-grants                        201 the dev-wallet allocation
    POST /operator/v1/deployments/{id}/dev-rate                201 the internal dev card

Each mutation is one of `LabControl`'s operator operations (approve, reject, rollback, fund,
price_dev) - no rule is restated here: the store compare-and-sets the listing at the expected
version (two racing approvals: one winner, the other 409), writes the audit event and keeps
admitted jobs' pins (a new listing version, R62/R69). What this module adds is the HTTP door:
the operator is a verified session whose `profiles.is_operator` the store re-reads
(`Operations.operator`), every mutation takes an `Idempotency-Key` (`once`, over 0060), and an
approval needs the candidate's readiness receipt from AP-05 (`Readiness`; none composed: 503).

`model_id` is the public alias with its slash (`nemostation/marlin-2b`): the path's fixed
`/rollback` suffix ends it, so nothing is split across segments ambiguously. Mounted only when
the composition puts a `Publication` on `rt.lab_publication` (switch off by default).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from pydantic import Field

from ...console.actions import operator as session_operator
from ...contracts import api, errors
from ...contracts.v2.records import (DeploymentRevision, DeploymentState, RateCardSnapshot,
                                     ServingRevision)
from ...operations.service import OperatorSession
from ...state.control_ops import ControlOps, Operation, input_hash
from .. import control
from .lab_control import IdempotencyKey, Proposal

if TYPE_CHECKING:                      # L3's port imports this package's lab_control
    from ...lab.control.operations import Operations

ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": api.ErrorEnvelope} for status in (401, 403, 404, 409, 410, 422, 503)}
LEASE_S = 60                     # a request that died holding the key frees it after this
_RATE = Field(pattern=r"^\d+(\.\d+)?$", max_length=40)
_REASON = Field(min_length=1, max_length=500)
_VERSION = Field(min_length=1, max_length=100)


# --------------------------------------------------------------------- ports
class ReadinessReceipt(api.Wire):
    """AP-05's readiness of one deployment revision, as this lane consumes it (by protocol
    until AP-05 merges): ready only when the served identities matched this revision."""

    deployment_revision_id: str
    serving_version_id: str
    ready: bool
    checked_at: str


class Readiness(Protocol):
    async def receipt(self, deployment_revision_id: str) -> ReadinessReceipt | None:
        """The newest readiness receipt of the deployment revision, or None."""
        ...


class DevKey(api.Wire):
    key_id: str
    endpoint_id: str
    name: str
    prefix: str
    created_at: str
    revoked_at: str | None = None


class DevWallet(api.Wire):
    """The provider's private dev wallet (CREDIT; never PROVIDER_USD): opened at 0 by the
    first operator allocation."""

    provider_org_id: str
    opened: bool
    balance: api.Money


class DevCredentials(Protocol):
    """SR-AP06-1: the provider_dev key rows and dev wallet `ControlStore` has no read for."""

    async def keys(self, provider_org_id: str, endpoint_id: str) -> Sequence[DevKey]: ...
    async def revoke(self, provider_org_id: str, endpoint_id: str, key_id: str, *,
                     actor: str, idempotency_key: str | None) -> DevKey: ...
    async def wallet(self, provider_org_id: str) -> DevWallet: ...


@dataclass(frozen=True)
class Publication:
    """What the publication routes stand on: L3's route port (`operations.control` is the
    `LabControl`, `operations.reads` its `ControlReads`), 0060's receipts, AP-05's readiness
    and SR-AP06-1's reads (each None until composed: those calls are 503)."""

    operations: Operations
    ops: ControlOps
    readiness: Readiness | None = None
    credentials: DevCredentials | None = None


# --------------------------------------------------------------------- idempotency
def _by_code() -> dict[str, type[errors.DomainError]]:
    """Each public code's most general class (a parent before its subclasses)."""
    found: dict[str, type[errors.DomainError]] = {}
    queue: list[type[errors.DomainError]] = [errors.DomainError]
    while queue:
        cls = queue.pop(0)
        found.setdefault(cls.code, cls)
        queue.extend(cls.__subclasses__())
    return found


_CODES = _by_code()


async def once[T](ops: ControlOps, actor: api.Actor, kind: str, key: str, body: dict,
                  work: Callable[[Operation], Awaitable[T]],
                  done: Callable[[Operation], Awaitable[T | None]], rid: str) -> tuple[T, bool]:
    """R270's Idempotency-Key for a synchronous control mutation, over 0060's receipts (no
    second store): the key is scoped to the actor's tenant + `kind` and bound to the body's
    hash (another body: 409). A finished first request answers again - its committed effect
    re-read by `done` (succeeded) or its refusal (failed) - and `replayed` says so. A request
    that died mid-way leaves its operation unfinished: the next one with the key takes the
    lease, asks `done` whether the effect committed, and only then runs `work`. A retryable
    refusal (an outage) is not recorded, so the same key retries. -> (outcome, replayed)."""
    started = await ops.start(kind, actor, key, input_hash(body))
    op = started.operation
    if op.state == "succeeded":
        found = await done(op)
        if found is None:
            raise errors.Gone("the outcome of this key is no longer readable")
        return found, True
    if op.state == "failed" and op.error is not None:
        raise _CODES.get(op.error.code, errors.Conflict)("replayed refusal")
    op = await ops.lease(op.operation_id, f"api:{uuid.uuid4()}", LEASE_S)
    try:
        found = await done(op) if started.replayed else None
        result = found if found is not None else await work(op)
    except errors.DomainError as refused:
        if not api.status_of(refused)[2]:
            await ops.finish(op.operation_id, op.fence, "failed",
                             api.envelope(refused, rid).error)
        raise
    await ops.finish(op.operation_id, op.fence, "succeeded")
    return result, started.replayed


# --------------------------------------------------------------------- wire
class Approval(api.Wire):
    expected_version: int | None = Field(ge=1)     # required: null = the alias's first listing
    rate_card_version: str = _VERSION
    input_rate: str = _RATE
    output_rate: str = _RATE
    reason: str = _REASON


class Rejection(api.Wire):
    reason: str = _REASON


class Rollback(api.Wire):
    to_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)
    reason: str = _REASON


class Allocation(api.Wire):
    provider_org_id: uuid.UUID
    amount: str = _RATE
    reason: str = _REASON


class DevRate(api.Wire):
    rate_card_version: str = _VERSION
    input_rate: str = _RATE
    output_rate: str = _RATE


class ListingDoc(api.Wire):
    public_model_id: str
    version: int
    deployment_revision_id: str
    rate_card_version: str
    replayed: bool = False


class Candidate(api.Wire):
    deployment_revision_id: str
    serving_version_id: str
    public_model_id: str
    revision_label: str
    runtime_image_ref: str
    max_input_tokens: int
    max_output_tokens: int
    state: str


class CardDoc(api.Wire):
    rate_card_version: str
    deployment_revision_id: str
    input_rate_per_million: api.Money
    output_rate_per_million: api.Money
    effective_at: str
    replayed: bool = False


class ProposalDetail(api.Wire):
    """Everything an operator decides on: the exact candidate, the readiness of the private
    revision it was proposed from, and the alias's current listing and card (the diff)."""

    proposal: Proposal
    provider_org_id: str
    candidate: Candidate
    readiness: ReadinessReceipt | None
    readiness_availability: api.Availability
    current_listing: ListingDoc | None
    current_card: CardDoc | None


class Allocated(api.Wire):
    entry_id: str
    provider_org_id: str
    amount: api.Money
    replayed: bool


def _money(amount: object) -> api.Money:
    return api.Money(amount=str(amount), unit="CREDIT")


def _card(card: RateCardSnapshot, replayed: bool = False) -> CardDoc:
    return CardDoc(rate_card_version=card.rate_card_version,
                   deployment_revision_id=card.deployment_revision_id,
                   input_rate_per_million=_money(card.input_rate_per_million),
                   output_rate_per_million=_money(card.output_rate_per_million),
                   effective_at=card.effective_at.isoformat(), replayed=replayed)


def _listing(listing: Any, replayed: bool = False) -> ListingDoc:
    return ListingDoc(public_model_id=listing.public_model_id, version=listing.version,
                      deployment_revision_id=listing.deployment_revision_id,
                      rate_card_version=listing.rate_card_version, replayed=replayed)


# --------------------------------------------------------------------- the service
class Decisions:
    """The operator half over one `Publication`."""

    def __init__(self, pub: Publication) -> None:
        self.pub = pub

    @property
    def control(self):
        return self.pub.operations.control

    async def operator(self, actor: api.Actor) -> OperatorSession:
        """A session whose actor says operator AND whose `profiles.is_operator` the store
        re-reads now: either alone is refused."""
        return await self.pub.operations.operator(session_operator(actor))

    async def _proposal(self, proposal_id: str) -> tuple[Proposal, DeploymentRevision]:
        """A Lab publication proposal (`lab_propose`) and its candidate revision; anything
        else - an unknown id, a dev or seeded revision - is not_found."""
        d = await self.control.store.deployment(proposal_id)
        found = d and [p for p in await self.pub.operations._proposals(d.provider_org_id)
                       if p.proposal_id == proposal_id]
        if not d or not found:
            raise errors.NotFound("no such publication proposal")
        return found[0], d

    async def _serving(self, d: DeploymentRevision) -> ServingRevision:
        serving = await self.control.catalog.serving_revision(d.serving_version_id)
        if serving is None:                    # a revision always has one (0007's FK)
            raise errors.NotFound("no such serving revision")
        return serving

    async def _alias(self, d: DeploymentRevision) -> str:
        return (await self._serving(d)).public_model_id

    async def proposals(self, actor: api.Actor, provider_org_id: str) -> list[Proposal]:
        await self.operator(actor)
        return await self.pub.operations._proposals(provider_org_id)

    async def detail(self, actor: api.Actor, proposal_id: str) -> ProposalDetail:
        await self.operator(actor)
        proposal, d = await self._proposal(proposal_id)
        serving = await self._serving(d)
        receipt, availability = None, api.Availability(state="disabled",
                                                       reason="readiness_not_composed")
        if self.pub.readiness is not None:
            try:
                receipt = await self.pub.readiness.receipt(proposal.deployment_revision_id)
                availability = api.Availability(state="configured")
            except errors.DependencyUnavailable:
                availability = api.Availability(state="unavailable", reason="readiness_unreachable")
        versions = await self.pub.operations.reads.listing_versions(serving.public_model_id)
        current = versions[-1] if versions else None
        card = None if current is None else await self.control.catalog.active_rate_card(
            current.deployment_revision_id)
        return ProposalDetail(
            proposal=proposal, provider_org_id=d.provider_org_id, candidate=Candidate(
                deployment_revision_id=d.deployment_revision_id,
                serving_version_id=d.serving_version_id,
                public_model_id=serving.public_model_id, revision_label=serving.revision_label,
                runtime_image_ref=serving.runtime_image_ref,
                max_input_tokens=d.max_input_tokens, max_output_tokens=d.max_output_tokens,
                state=d.state.value),
            readiness=receipt, readiness_availability=availability,
            current_listing=None if current is None else _listing(current),
            current_card=None if card is None else _card(card))

    async def _ready(self, proposal: Proposal, candidate: DeploymentRevision) -> None:
        """The private revision the proposal came from is still validated and AP-05 measured
        it serving this exact serving revision; a stale or failed receipt is a 409."""
        if self.pub.readiness is None:
            raise errors.DependencyUnavailable("deployment readiness is not composed")
        source = await self.control.store.deployment(proposal.deployment_revision_id)
        receipt = await self.pub.readiness.receipt(proposal.deployment_revision_id)
        if (source is None or source.state is not DeploymentState.ready_private or receipt is None
                or not receipt.ready
                or receipt.deployment_revision_id != proposal.deployment_revision_id
                or receipt.serving_version_id != candidate.serving_version_id):
            raise errors.StateConflict("the candidate has no current readiness receipt")

    async def approve(self, actor: api.Actor, proposal_id: str, body: Approval, key: str,
                      rid: str) -> ListingDoc:
        operator = await self.operator(actor)
        proposal, candidate = await self._proposal(proposal_id)
        alias = await self._alias(candidate)

        async def work(_op: Operation):
            await self._ready(proposal, candidate)
            return await self.control.approve(
                operator, proposal_id, rate_card_version=body.rate_card_version,
                input_rate=body.input_rate, output_rate=body.output_rate,
                expected_version=body.expected_version, reason=body.reason)

        async def done(_op: Operation):
            listed = await self.pub.operations.reads.listing_versions(alias)
            return next((v for v in listed if v.deployment_revision_id == proposal_id), None)

        listing, replayed = await once(
            self.pub.ops, actor, "publication.approve", key,
            {"proposal_id": proposal_id, **body.model_dump(mode="json")}, work, done, rid)
        return _listing(listing, replayed)

    async def reject(self, actor: api.Actor, proposal_id: str, body: Rejection, key: str,
                     rid: str) -> Proposal:
        operator = await self.operator(actor)
        proposal, _ = await self._proposal(proposal_id)

        async def work(_op: Operation):
            return await self.pub.operations.reject(operator, proposal_id, body.reason)

        async def done(_op: Operation):
            now, _ = await self._proposal(proposal_id)
            return now if now.state == "rejected" else None

        decided, _ = await once(self.pub.ops, actor, "publication.reject", key,
                                {"proposal_id": proposal.proposal_id, "reason": body.reason},
                                work, done, rid)
        return decided

    async def rollback(self, actor: api.Actor, model_id: str, body: Rollback, key: str,
                       rid: str) -> ListingDoc:
        operator = await self.operator(actor)
        reads = self.pub.operations.reads

        async def work(_op: Operation):
            return await self.control.rollback(
                operator, model_id, to_version=body.to_version,
                expected_version=body.expected_version, reason=body.reason)

        async def done(_op: Operation):
            listed = {v.version: v for v in await reads.listing_versions(model_id)}
            made, target = listed.get(body.expected_version + 1), listed.get(body.to_version)
            return made if made and target and \
                made.deployment_revision_id == target.deployment_revision_id else None

        listing, replayed = await once(
            self.pub.ops, actor, "listing.rollback", key,
            {"model_id": model_id, **body.model_dump(mode="json")}, work, done, rid)
        return _listing(listing, replayed)

    async def fund(self, actor: api.Actor, body: Allocation, key: str, rid: str) -> Allocated:
        operator = await self.operator(actor)
        provider = str(body.provider_org_id)

        async def work(op: Operation):
            # the operation id is the allocation's: a redo is grant_credit's own replay
            return await self.control.fund(operator, provider, body.amount,
                                           idempotency_key=op.operation_id, reason=body.reason)

        entry, replayed = await once(self.pub.ops, actor, "dev_wallet.grant", key,
                                     body.model_dump(mode="json"), work, work, rid)
        return Allocated(entry_id=entry.entry_id, provider_org_id=provider,
                         amount=_money(entry.amount), replayed=replayed)

    async def dev_rate(self, actor: api.Actor, deployment_revision_id: str, body: DevRate,
                       key: str, rid: str) -> CardDoc:
        operator = await self.operator(actor)

        async def work(_op: Operation):
            return await self.control.price_dev(
                operator, deployment_revision_id, rate_card_version=body.rate_card_version,
                input_rate=body.input_rate, output_rate=body.output_rate)

        async def done(_op: Operation):
            card = await self.control.catalog.active_rate_card(deployment_revision_id)
            return card if card and card.rate_card_version == body.rate_card_version else None

        card, replayed = await once(
            self.pub.ops, actor, "dev_rate.set", key,
            {"deployment_revision_id": deployment_revision_id, **body.model_dump(mode="json")},
            work, done, rid)
        return _card(card, replayed)


# --------------------------------------------------------------------- routes
def register(app: FastAPI, rt: Any) -> Decisions | None:
    pub = getattr(rt, "lab_publication", None)
    if pub is None:
        return None
    decisions = Decisions(pub)
    router = APIRouter(route_class=control.R270Route, responses=ERRORS)

    async def actor(request: Request) -> api.Actor:
        return await rt.actors.actor(request)

    who = Depends(actor)
    base = "/operator/v1/publication-proposals"

    @router.get(base, response_model=api.ListPage[Proposal], operation_id="listPublicationProposals")
    async def list_proposals(provider_org_id: uuid.UUID = Query(), a: api.Actor = who):
        # ponytail: one provider's few proposals, unpaged; a cursor when a provider has many
        return control.ok(api.ListPage[Proposal](
            data=tuple(await decisions.proposals(a, str(provider_org_id)))))

    @router.get(base + "/{proposal_id}", response_model=ProposalDetail,
                operation_id="getPublicationProposal")
    async def get_proposal(proposal_id: str, a: api.Actor = who):
        return control.ok(await decisions.detail(a, proposal_id))

    @router.post(base + "/{proposal_id}/approve", response_model=ListingDoc,
                 operation_id="approvePublicationProposal")
    async def approve(request: Request, proposal_id: str, body: Approval, key: IdempotencyKey,
                      a: api.Actor = who):
        return control.ok(await decisions.approve(a, proposal_id, body, key,
                                                  control.request_id(request)))

    @router.post(base + "/{proposal_id}/reject", response_model=Proposal,
                 operation_id="rejectPublicationProposal")
    async def reject(request: Request, proposal_id: str, body: Rejection, key: IdempotencyKey,
                     a: api.Actor = who):
        return control.ok(await decisions.reject(a, proposal_id, body, key,
                                                 control.request_id(request)))

    @router.post("/operator/v1/listings/{model_id:path}/rollback", response_model=ListingDoc,
                 operation_id="rollbackListing")
    async def rollback(request: Request, model_id: str, body: Rollback, key: IdempotencyKey,
                       a: api.Actor = who):
        return control.ok(await decisions.rollback(a, model_id, body, key,
                                                   control.request_id(request)))

    @router.post("/operator/v1/dev-wallet-grants", response_model=Allocated, status_code=201,
                 operation_id="grantDevWallet")
    async def fund(request: Request, body: Allocation, key: IdempotencyKey, a: api.Actor = who):
        done = await decisions.fund(a, body, key, control.request_id(request))
        return control.ok(done, 200 if done.replayed else 201)

    @router.post("/operator/v1/deployments/{deployment_revision_id}/dev-rate",
                 response_model=CardDoc, status_code=201, operation_id="setDevRate")
    async def dev_rate(request: Request, deployment_revision_id: str, body: DevRate,
                       key: IdempotencyKey, a: api.Actor = who):
        card = await decisions.dev_rate(a, deployment_revision_id, body, key,
                                        control.request_id(request))
        return control.ok(card, 200 if card.replayed else 201)

    app.include_router(router)
    return decisions
