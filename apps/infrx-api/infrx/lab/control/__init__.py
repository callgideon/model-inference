"""L3: provider model registration and dev/prod revision services (LAB-PUBLISH, LAB-ACCESS).

The Lab's authorized door onto A3's registry. It adds no catalog: rows are written through
A3's `Registry` (immutable `put`) and read through A3's `CatalogDirectory`, so admission,
`/v1/models` and this service resolve one set of rows. What A3 has no statement for - a state
move, a proposal, a listing version, a dev credential, a dev-wallet allocation - is one atomic
call of lab-sql's L3-SQL seam (`ControlStore`), which does its compare-and-set and writes its
audit event in the same transaction. The service decides who may ask (L2's current membership
and role, on the store clock) and what may be registered; the store decides what may move.

    provider (server session user_id, L2)   register, create_dev, validate, issue_dev_key,
                                            propose, events
    operator (`Operations.operator(secret)`) approve, rollback, price_dev, fund

A dev revision is private, in a `dev` endpoint of its provider, reachable only by that
endpoint's provider_dev credential (A3's resolution), priced only by an operator's internal
card and spent only from the provider's dev wallet - which opens at 0 CREDIT and is funded
only by audited operator allocations. Publication is a NEW public revision the provider's
administrator proposes and an operator approves; approval and rollback each add a listing
version, so an admitted job keeps the pins it was admitted with (R62/R69).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ...contracts import errors
from ...contracts.v2.money_units import Credit
from ...contracts.v2.ports import CatalogDirectory
from ...contracts.v2.records import (CreditLedgerEntry, DeploymentRevision, DeploymentState,
                                     Environment, ProviderCapability, RateCardSnapshot,
                                     ServingRevision, Visibility)
from ...operations.ports import Registry
from ...operations.service import (PREFIX_CHARS, IssuedKey, OperatorSession, hash_key,
                                   new_secret, stable_id)
from ..access import LabAccess

# What a provider may run: a supported image, pinned by digest (never a moving tag or an
# uploaded image), serving the request/response schemas the gateway validates.
# ponytail: one runtime and one schema pair; widen with the runtime adapter (G/W wiring).
RUNTIME = re.compile(r"^(?P<repo>[a-z0-9][a-z0-9./_-]*)@sha256:[0-9a-f]{64}$")
SUPPORTED_RUNTIMES = frozenset({"vllm/vllm-openai"})
SUPPORTED_SCHEMAS = frozenset({("infrx.request.chat.v1", "infrx.response.chat.v1")})


class Listing(BaseModel):
    """One catalog listing version (0007 `catalog_listings`): the alias's current target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    public_model_id: str
    version: int = Field(ge=1)
    deployment_revision_id: str
    rate_card_version: str


class ControlEvent(BaseModel):
    """One control audit event, written by the store in the transaction it records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str        # lab_transition | lab_propose | lab_dev_key | lab_publish | lab_rollback
    actor: str         # | lab_fund
    provider_org_id: str
    subject: str
    before: dict[str, Any] | None = None
    after: dict[str, Any]
    at: datetime


class ControlStore(Protocol):
    """lab-sql's L3-SQL seam (the schema request in the L3 evidence). Every write is one
    transaction with its audit event; refusals are typed (`not_found` for another provider's
    or an unknown row, `state_conflict` for a stale compare-and-set)."""

    async def db_now(self) -> datetime:
        """`infrx.now()`, the one clock (R7)."""

    async def model_provider(self, model_id: str) -> str | None:
        """The provider that owns `public.models.model_uuid = model_id`, or None."""

    async def deployment(self, deployment_revision_id: str) -> DeploymentRevision | None:
        """Any deployment revision by id, whatever its visibility (the caller checks whose)."""

    async def endpoint(self, provider_org_id: str, name: str, environment: Environment,
                       actor: str) -> str:
        """The provider's endpoint of this name and environment, created if absent."""

    async def transition(self, deployment_revision_id: str, provider_org_id: str, *,
                         expected: DeploymentState, to: DeploymentState, actor: str,
                         reason: str) -> DeploymentRevision:
        """CAS on the state (0007's graph); `lab_transition`."""

    async def propose(self, proposal: DeploymentRevision, *, source_revision_id: str,
                      actor: str) -> DeploymentRevision:
        """Insert the public `proposed_public` prod revision of a `ready_private` dev source
        of the same provider and serving version; `lab_propose`."""

    async def issue_dev_key(self, *, provider_org_id: str, endpoint_id: str, user_id: str,
                            key_hash: str, prefix: str, name: str) -> str:
        """A provider_dev `api_keys` row scoped to the provider's dev endpoint; `lab_dev_key`."""

    async def publish(self, public_model_id: str, card: RateCardSnapshot, *,
                      expected_version: int | None, actor: str, reason: str) -> Listing:
        """CAS on the alias's current version: insert the card, move the card's
        `proposed_public` deployment to `active`, add listing version + 1; `lab_publish`."""

    async def rollback(self, public_model_id: str, *, to_version: int, expected_version: int,
                       actor: str, reason: str) -> Listing:
        """CAS: listing version + 1 repeating an earlier version whose deployment is still
        active; `lab_rollback`."""

    async def fund_dev_wallet(self, provider_org_id: str, amount: Credit, *, operation_id: str,
                              actor: str, reason: str) -> CreditLedgerEntry:
        """Open the provider's dev wallet at 0 if absent, then one `operator_allocation`
        (`grant_credit`, replayed by operation id); `lab_fund`."""

    async def events(self, provider_org_id: str) -> Sequence[ControlEvent]:
        """The provider's control audit events, oldest first."""


class SmokeTest(Protocol):
    """The engine's dev smoke (G/W runtime adapter, wiring): True when the revision serves."""

    async def smoke(self, serving: ServingRevision, deployment: DeploymentRevision) -> bool: ...


def unsupported(serving: ServingRevision) -> str | None:
    """Why this serving revision may not be registered, or None."""
    runtime = RUNTIME.match(serving.runtime_image_ref)
    if runtime is None:
        return "the runtime image is registered by digest (<repo>@sha256:<hex>), never a tag"
    if runtime["repo"] not in SUPPORTED_RUNTIMES:
        return f"runtime {runtime['repo']} is not a supported runtime"
    capability = serving.capability
    if (capability.input_schema_ref, capability.output_schema_ref) not in SUPPORTED_SCHEMAS:
        return "the request/response schemas are not ones the gateway serves"
    return None


class LabControl:
    """Named control operations. `user_id` is the server-verified session identity."""

    def __init__(self, access: LabAccess, store: ControlStore, registry: Registry,
                 catalog: CatalogDirectory, engine: SmokeTest) -> None:
        self.access, self.store, self.registry = access, store, registry
        self.catalog, self.engine = catalog, engine

    async def _dev(self, user_id: str, provider_org_id: str, deployment_revision_id: str,
                   capability: ProviderCapability) -> DeploymentRevision:
        """The provider's own dev revision, for a member holding `capability`; anything else
        is `not_found` (never a 403 that confirms another provider's row)."""
        await self.access._member(user_id, provider_org_id, capability)
        deployment = await self.store.deployment(deployment_revision_id)
        if (deployment is None or deployment.provider_org_id != provider_org_id
                or deployment.environment is not Environment.dev):
            raise errors.NotFound("no such dev revision in this provider workspace")
        return deployment

    # --- provider operations ------------------------------------------------------
    async def register(self, user_id: str, provider_org_id: str,
                       serving: ServingRevision) -> bool:
        """A serving revision of one of the provider's own models (A3's immutable `put`:
        True written, False already there, `Conflict` another row under its id)."""
        await self.access._member(user_id, provider_org_id,
                                  ProviderCapability.manage_dev_deployment)
        if serving.provider_org_id != provider_org_id:
            raise errors.NotFound("no such model in this provider workspace")
        if await self.store.model_provider(serving.model_id) != provider_org_id:
            raise errors.NotFound("no such model in this provider workspace")
        refusal = unsupported(serving)
        if refusal:
            raise errors.InvalidRequest(refusal)
        return await self.registry.put(serving)

    async def create_dev(self, user_id: str, provider_org_id: str, *, serving_version_id: str,
                         endpoint_name: str, max_input_tokens: int,
                         max_output_tokens: int) -> DeploymentRevision:
        """A private `draft` revision of the provider's own serving revision on its dev
        endpoint `endpoint_name`."""
        await self.access._member(user_id, provider_org_id,
                                  ProviderCapability.manage_dev_deployment)
        serving = await self.catalog.serving_revision(serving_version_id)
        if serving is None or serving.provider_org_id != provider_org_id:
            raise errors.NotFound("no such serving revision in this provider workspace")
        endpoint_id = await self.store.endpoint(provider_org_id, endpoint_name, Environment.dev,
                                                actor=user_id)
        deployment = DeploymentRevision(
            deployment_revision_id=str(uuid.uuid4()), endpoint_id=endpoint_id,
            provider_org_id=provider_org_id, serving_version_id=serving_version_id,
            environment=Environment.dev, visibility=Visibility.private,
            state=DeploymentState.draft, max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens, created_at=await self.store.db_now())
        await self.registry.put(deployment)
        return deployment

    async def validate(self, user_id: str, provider_org_id: str,
                       deployment_revision_id: str) -> DeploymentRevision:
        """draft -> validating -> the engine's smoke -> ready_private, or retired on a
        failure (a failed validation never becomes public). ponytail: a smoke interrupted
        mid-way leaves the revision validating; retire it and create another."""
        deployment = await self._dev(user_id, provider_org_id, deployment_revision_id,
                                     ProviderCapability.manage_dev_deployment)
        serving = await self.catalog.serving_revision(deployment.serving_version_id)
        deployment = await self.store.transition(
            deployment_revision_id, provider_org_id, expected=DeploymentState.draft,
            to=DeploymentState.validating, actor=user_id, reason="dev smoke started")
        passed = await self.engine.smoke(serving, deployment)
        return await self.store.transition(
            deployment_revision_id, provider_org_id, expected=DeploymentState.validating,
            to=DeploymentState.ready_private if passed else DeploymentState.retired,
            actor=user_id, reason="dev smoke passed" if passed else "dev smoke failed")

    async def issue_dev_key(self, user_id: str, provider_org_id: str,
                            deployment_revision_id: str, name: str = "dev") -> IssuedKey:
        """A provider_dev credential for the validated revision's dev endpoint. The secret
        is returned once; the store keeps its hash."""
        deployment = await self._dev(user_id, provider_org_id, deployment_revision_id,
                                     ProviderCapability.manage_dev_deployment)
        if deployment.state is not DeploymentState.ready_private:
            raise errors.StateConflict("only a validated dev revision gets a credential")
        secret = new_secret()
        key_id = await self.store.issue_dev_key(
            provider_org_id=provider_org_id, endpoint_id=deployment.endpoint_id,
            user_id=user_id, key_hash=hash_key(secret), prefix=secret[:PREFIX_CHARS], name=name)
        return IssuedKey(key_id=key_id, org_id=provider_org_id, prefix=secret[:PREFIX_CHARS],
                         secret=secret)

    async def propose(self, user_id: str, provider_org_id: str, deployment_revision_id: str, *,
                      endpoint_name: str) -> DeploymentRevision:
        """The administrator's publication proposal: a NEW public revision of the validated
        dev revision's serving version on the prod endpoint `endpoint_name`, not yet listed."""
        source = await self._dev(user_id, provider_org_id, deployment_revision_id,
                                 ProviderCapability.propose_publication)
        endpoint_id = await self.store.endpoint(provider_org_id, endpoint_name,
                                                Environment.prod, actor=user_id)
        proposal = DeploymentRevision(
            deployment_revision_id=str(uuid.uuid4()), endpoint_id=endpoint_id,
            provider_org_id=provider_org_id, serving_version_id=source.serving_version_id,
            environment=Environment.prod, visibility=Visibility.public,
            state=DeploymentState.proposed_public, max_input_tokens=source.max_input_tokens,
            max_output_tokens=source.max_output_tokens, created_at=await self.store.db_now())
        return await self.store.propose(proposal, source_revision_id=deployment_revision_id,
                                        actor=user_id)

    async def events(self, user_id: str, provider_org_id: str) -> tuple[ControlEvent, ...]:
        """The provider's control history (it names members and operators: developer+)."""
        await self.access._member(user_id, provider_org_id,
                                  ProviderCapability.manage_dev_deployment)
        return tuple(await self.store.events(provider_org_id))

    # --- operator operations --------------------------------------------------------
    async def _card(self, operator: OperatorSession, deployment: DeploymentRevision,
                    rate_card_version: str, input_rate: str,
                    output_rate: str) -> RateCardSnapshot:
        serving = await self.catalog.serving_revision(deployment.serving_version_id)
        try:
            return RateCardSnapshot(
                rate_card_version=rate_card_version, model_id=serving.model_id,
                deployment_revision_id=deployment.deployment_revision_id,
                serving_version_id=deployment.serving_version_id,
                input_rate_per_million=input_rate, output_rate_per_million=output_rate,
                effective_at=await self.store.db_now(), approved_by=operator.principal)
        except ValueError as refused:
            raise errors.InvalidRequest(f"not a card: {refused}") from None

    async def approve(self, operator: OperatorSession, deployment_revision_id: str, *,
                      rate_card_version: str, input_rate: str, output_rate: str,
                      expected_version: int | None, reason: str) -> Listing:
        """Publish a proposal under its serving revision's public model id at an approved
        card, if the listing is still at `expected_version`."""
        deployment = await self.store.deployment(deployment_revision_id)
        if deployment is None:
            raise errors.NotFound("no such deployment revision")
        card = await self._card(operator, deployment, rate_card_version, input_rate, output_rate)
        serving = await self.catalog.serving_revision(deployment.serving_version_id)
        return await self.store.publish(serving.public_model_id, card,
                                        expected_version=expected_version,
                                        actor=operator.principal, reason=reason)

    async def rollback(self, operator: OperatorSession, public_model_id: str, *,
                       to_version: int, expected_version: int, reason: str) -> Listing:
        return await self.store.rollback(public_model_id, to_version=to_version,
                                         expected_version=expected_version,
                                         actor=operator.principal, reason=reason)

    async def price_dev(self, operator: OperatorSession, deployment_revision_id: str, *,
                        rate_card_version: str, input_rate: str,
                        output_rate: str) -> RateCardSnapshot:
        """The internal CREDIT card of a validated private dev revision (no preview is
        unmetered; a draft or validating one is never priced). A public revision is priced
        only by `approve`."""
        deployment = await self.store.deployment(deployment_revision_id)
        if deployment is None:
            raise errors.NotFound("no such deployment revision")
        if deployment.visibility is not Visibility.private:
            raise errors.InvalidRequest("an internal card prices a private dev revision only")
        if deployment.state is not DeploymentState.ready_private:
            raise errors.StateConflict("only a validated dev revision is priced")
        card = await self._card(operator, deployment, rate_card_version, input_rate, output_rate)
        await self.registry.put(card)
        return card

    async def fund(self, operator: OperatorSession, provider_org_id: str, amount: str, *,
                   idempotency_key: str, reason: str) -> CreditLedgerEntry:
        """An audited operator allocation to the provider's dev wallet; the same key is
        the same allocation (its operation id), never a second one."""
        try:
            value = Credit(amount)
        except ValueError:
            raise errors.InvalidRequest("an allocation is exact CREDIT text") from None
        return await self.store.fund_dev_wallet(
            provider_org_id, value, operation_id=stable_id("lab_fund", idempotency_key),
            actor=operator.principal, reason=reason)


__all__ = ["ControlEvent", "ControlStore", "LabControl", "Listing", "SmokeTest", "unsupported"]
