"""L3's adapters for its consumers, over `LabControl` (never a second rule set).

    Operations   WR-LAB-API-2: `routes/lab_control.py`'s `ControlOperations` port. The route
                 hands over an `Actor` it derived from the session; L3 re-asks L2 for the
                 same (user, provider) on every call (`LabAccess.require`, via `LabControl`)
                 and ignores the claimed role.
    Serving      WR-R2-2: R2's `ServingControl` - an endpoint alias's current serving ref and
                 listing version (the fence), and a rollback that is the store's audited
                 listing CAS (`LabControl.rollback`): admitted jobs keep their pins (R62/R69).

The route's records are closed and coarser than L3's (`state` is liveness only; `smoke`
carries validation; `visibility` is whether the alias's current listing names the revision,
E3L-F5): see `_deployment`. A registration is the Lab App's form (0-L3I-R1): `name`
is the model's bare name in the workspace (the App allows no slug), `artifact_digest` the
model's weights - one of the shard digests an operator imported it with, checked, never
re-declared - and `runtime` the image by digest (`<repo>@sha256:<hex>`). It makes a new serving
revision over those same weights (a model's first weights are an operator's import). A provider `rollback` proposal is refused: rollback is an operator's
(or R2's) listing CAS.

The listings need reads `ControlStore` lacks (`ControlReads`, lab-sql's WR-LSQ-9).
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Protocol, Sequence

from ...contracts import errors
from ...contracts.lab.records import canonical
from ...contracts.v2.records import (DeploymentRevision, DeploymentState, ProviderCapability,
                                     ServingRevision)
# WR-LAB-API-2b: the route's own closed records, not copies
from ...gateway.routes.lab_control import Actor, Deployment, Model, Proposal, Registration
from ...operations.service import OperatorSession
from . import RUNTIME, LabControl, Listing

S = DeploymentState


class ControlOperations(Protocol):
    async def models(self, actor: Actor) -> Sequence[Model]: ...
    async def deployments(self, actor: Actor) -> Sequence[Deployment]: ...
    async def proposals(self, actor: Actor) -> Sequence[Proposal]: ...
    async def register(self, actor: Actor, registration: Registration) -> Deployment: ...
    async def smoke(self, actor: Actor, deployment_revision_id: str) -> Deployment: ...
    async def propose(self, actor: Actor, kind: str, deployment_revision_id: str) -> Proposal: ...
    async def operator(self, user_id: str) -> OperatorSession: ...
    async def reject(self, operator: OperatorSession, proposal_id: str,
                     reason: str) -> Proposal: ...


class ControlReads(Protocol):
    """WR-LSQ-9 (lab-sql, `PgControlStore`): plain reads of 0007's rows."""

    async def provider_servings(self, provider_org_id: str) -> Sequence[ServingRevision]: ...
    async def provider_deployments(self, provider_org_id: str) -> Sequence[DeploymentRevision]:
        ...
    async def endpoint_alias(self, endpoint_id: str) -> str | None:
        """The public model id whose newest listing names a deployment on this endpoint."""
    async def listing_versions(self, public_model_id: str) -> Sequence[Listing]:
        """Every listing version of the alias, oldest first."""


# --- the Lab's control port --------------------------------------------------------------
REQUEST, RESPONSE = "infrx.request.", "infrx.response."


def _model(serving: ServingRevision) -> Model:
    return Model(model_id=serving.public_model_id, revision_label=serving.revision_label,
                 artifact_digest=serving.weight_shard_digests[0],
                 runtime=serving.runtime_image_ref, registered_at=serving.created_at,
                 schema_version=serving.capability.input_schema_ref.removeprefix(REQUEST))


class Operations:
    def __init__(self, control: LabControl, reads: ControlReads) -> None:
        self.control, self.reads = control, reads

    async def _deployment(self, d: DeploymentRevision) -> Deployment:
        serving = await self.control.catalog.serving_revision(d.serving_version_id)
        card = await self.control.catalog.active_rate_card(d.deployment_revision_id)
        failed = any(e.action == "lab_transition" and e.subject == d.deployment_revision_id
                     and e.after.get("state") == S.retired and (e.before or {}).get("state")
                     == S.validating for e in await self.control.store.events(d.provider_org_id))
        smoke = ("failed" if failed else "none" if d.state in (S.draft, S.validating, S.retired)
                 else "passed")
        # E3L-F5 (R207): public is the listing's truth - only the alias's current listing is;
        # a pending, rejected, rolled-back or replaced revision reads private (R189: coarse).
        versions = await self.reads.listing_versions(serving.public_model_id)
        listed = versions[-1].deployment_revision_id if versions else None
        return Deployment(
            deployment_revision_id=d.deployment_revision_id, model_id=serving.public_model_id,
            serving_version_id=d.serving_version_id, revision_label=serving.revision_label,
            runtime=serving.runtime_image_ref, created_at=d.created_at,
            schema_version=serving.capability.input_schema_ref.removeprefix(REQUEST),
            rate_card_version=card.rate_card_version if card else None,
            environment=d.environment.value,
            visibility="public" if d.deployment_revision_id == listed else "private",
            state="retired" if d.state is S.retired else "active", smoke=smoke)

    async def models(self, actor: Actor) -> list[Model]:
        await self.control.access.require(actor.user_id, actor.provider_org_id,
                                          ProviderCapability.read_aggregate_health)
        return [_model(s) for s in await self.reads.provider_servings(actor.provider_org_id)]

    async def deployments(self, actor: Actor) -> list[Deployment]:
        await self.control.access.require(actor.user_id, actor.provider_org_id,
                                          ProviderCapability.read_aggregate_health)
        return [await self._deployment(d)
                for d in await self.reads.provider_deployments(actor.provider_org_id)]

    async def proposals(self, actor: Actor) -> list[Proposal]:
        await self.control.access.require(actor.user_id, actor.provider_org_id,
                                          ProviderCapability.read_aggregate_health)
        return await self._proposals(actor.provider_org_id)

    async def _proposals(self, provider_org_id: str) -> list[Proposal]:
        """Every `lab_propose` of the provider; a proposal no operator listed and no longer
        `proposed_public` is `rejected` (E3L-F4: the operator's decision, or the platform's
        retirement - then with no instant)."""
        events = await self.control.store.events(provider_org_id)
        published = {e.after.get("deployment_revision_id"): e.at for e in events
                     if e.action == "lab_publish"}
        rejected = {e.subject: e.at for e in events if e.action == "lab_transition"
                    and (e.before or {}).get("state") == S.proposed_public}
        found = []
        for e in (e for e in events if e.action == "lab_propose"):
            d = await self.control.store.deployment(e.subject)
            decided = published.get(e.subject)
            state = ("proposed" if d.state is S.proposed_public
                     else "approved" if decided else "rejected")
            found.append(Proposal(proposal_id=e.subject, kind="publish",
                                  deployment_revision_id=e.after["source"], state=state,
                                  proposed_at=e.at,
                                  decided_at=decided or rejected.get(e.subject)))
        return found

    # --- the operator's door (E3L-F4) ---------------------------------------------------
    async def operator(self, user_id: str) -> OperatorSession:
        """The session user as a platform operator (`profiles.is_operator`), audited as
        `operator:<user_id>` (0025's naming); anyone else is `forbidden`."""
        if not await self.control.store.operator(user_id):
            raise errors.Forbidden("an operator decides a proposal")
        return OperatorSession(ops=None, principal=f"operator:{user_id}")

    async def reject(self, operator: OperatorSession, proposal_id: str,
                     reason: str) -> Proposal:
        """Decline a provider's open publication proposal; anything that is not one of the
        Lab's proposals is `not_found`."""
        d = await self.control.store.deployment(proposal_id)
        mine = d and [p for p in await self._proposals(d.provider_org_id)
                      if p.proposal_id == proposal_id]
        if not mine:
            raise errors.NotFound("no such proposal")
        await self.control.reject(operator, proposal_id, reason=reason)
        return next(p for p in await self._proposals(d.provider_org_id)
                    if p.proposal_id == proposal_id)

    async def register(self, actor: Actor, registration: Registration) -> Deployment:
        """A new serving revision of the provider's model over its pinned weights, then its
        private dev revision on the model's dev endpoint (`<slug>/<name>-dev`)."""
        user, provider = actor.user_id, actor.provider_org_id
        await self.control.access.require(user, provider, ProviderCapability.manage_dev_deployment)
        servings = [s for s in await self.reads.provider_servings(provider)
                    if s.public_model_id.rpartition("/")[2] == registration.name]
        if not servings:
            raise errors.NotFound("no model with registered weights of this name in this "
                                  "provider workspace")
        weighted = [s for s in servings
                    if registration.artifact_digest in s.weight_shard_digests]
        if not weighted:
            raise errors.InvalidRequest("the artifact digest is not one of this model's "
                                        "imported weights: a Lab registration declares none")
        base = max(weighted, key=lambda s: s.created_at)
        limits = [d for d in await self.reads.provider_deployments(provider)
                  if d.serving_version_id in {s.serving_version_id for s in servings}]
        if not limits:
            raise errors.InvalidRequest("this model has no deployed limits yet: an operator "
                                        "deploys its first revision")
        latest = max(limits, key=lambda d: d.created_at)
        serving = base.model_copy(update={
            "serving_version_id": str(uuid.uuid4()),
            # ponytail: labels by count; two concurrent registrations share one and the second
            # is refused by the registry, never merged.
            "revision_label": f"lab-{len(servings) + 1}",
            "runtime_image_ref": registration.runtime,
            # a malformed ref is left for `LabControl.register` to refuse (`unsupported`)
            "runtime_image_digest": (registration.runtime.partition("@")[2]
                                     if RUNTIME.match(registration.runtime) else None),
            "capability": base.capability.model_copy(update={
                "input_schema_ref": REQUEST + registration.schema_version,
                "output_schema_ref": RESPONSE + registration.schema_version}),
            "created_at": await self.control.store.db_now()})
        await self.control.register(user, provider, ServingRevision.model_validate(
            serving.model_dump()))
        dev = await self.control.create_dev(
            user, provider, serving_version_id=serving.serving_version_id,
            endpoint_name=registration.name,
            max_input_tokens=latest.max_input_tokens, max_output_tokens=latest.max_output_tokens)
        return await self._deployment(dev)

    async def smoke(self, actor: Actor, deployment_revision_id: str) -> Deployment:
        return await self._deployment(await self.control.validate(
            actor.user_id, actor.provider_org_id, deployment_revision_id))

    async def propose(self, actor: Actor, kind: str, deployment_revision_id: str) -> Proposal:
        if kind != "publish":
            raise errors.InvalidRequest("a rollback is an operator's listing decision, not a "
                                        "provider proposal")
        source = await self.control.store.deployment(deployment_revision_id)
        serving = source and await self.control.catalog.serving_revision(
            source.serving_version_id)
        proposal = await self.control.propose(
            actor.user_id, actor.provider_org_id, deployment_revision_id,
            endpoint_name=serving.public_model_id.rpartition("/")[2] if serving else "-")
        return Proposal(proposal_id=proposal.deployment_revision_id, kind="publish",
                        deployment_revision_id=deployment_revision_id, state="proposed",
                        proposed_at=proposal.created_at, decided_at=None)


# --- R2's serving control -----------------------------------------------------------------
def serving_ref(deployment: DeploymentRevision, serving: ServingRevision) -> str:
    """`lab:serving:<provider>:<deployment_revision_id>@sha256:<the serving revision's JCS
    digest>`: the listed deployment, pinned to the immutable revision it serves."""
    digest = hashlib.sha256(canonical(serving.model_dump(mode="json"))).hexdigest()
    return (f"lab:serving:{deployment.provider_org_id}:{deployment.deployment_revision_id}"
            f"@sha256:{digest}")


class Serving:
    """`ServingControl` for R2's controller, acting as `operator` (its audited principal)."""

    def __init__(self, control: LabControl, reads: ControlReads,
                 operator: OperatorSession) -> None:
        self.control, self.reads, self.operator = control, reads, operator

    async def _ref(self, listing: Listing) -> str:
        d = await self.control.store.deployment(listing.deployment_revision_id)
        return serving_ref(d, await self.control.catalog.serving_revision(d.serving_version_id))

    async def _versions(self, endpoint_id: str) -> tuple[str, Sequence[Listing]]:
        alias = await self.reads.endpoint_alias(endpoint_id)
        versions = await self.reads.listing_versions(alias) if alias else ()
        if not versions:
            raise errors.NotFound("no listed alias on this endpoint")
        return alias, versions

    async def serving(self, endpoint_id: str) -> tuple[str, int]:
        _, versions = await self._versions(endpoint_id)
        return await self._ref(versions[-1]), versions[-1].version

    async def rollback(self, endpoint_id: str, *, fence: int, to_serving_ref: str,
                       reason: str) -> int:
        """The newest listing version serving `to_serving_ref`, re-listed only if it is
        earlier and the alias is still at `fence` (the store's CAS: `StateConflict` on a
        stale fence, `NotFound` for a version that is not earlier)."""
        alias, versions = await self._versions(endpoint_id)
        for listing in reversed(versions):    # the store refuses the current and later ones
            if await self._ref(listing) == to_serving_ref:
                return (await self.control.rollback(
                    self.operator, alias, to_version=listing.version, expected_version=fence,
                    reason=reason)).version
        raise errors.NotFound("no earlier listing of this alias serves that ref")


__all__ = ["Actor", "ControlOperations", "ControlReads", "Deployment", "Model", "Operations",
           "Proposal", "Registration", "Serving", "serving_ref"]
