"""AP-05: durable private deployments (research/plan/api-lifecycle/contracts.md §5).

A private deployment IS L3's dev `DeploymentRevision` (0007: draft -> validating ->
ready_private -> retired, moved only through `lab_control_transition`); this package adds its
hosting: the one approved profile, the request (0062 `hosting_deployments`), the operations
(0060 `deployment.create|smoke|retire`), the allocation, the receipts, and the out-of-process
controller (`controller.Controller`) that does the work. `LabHosting` is what
`routes/lab_deployments.py` takes from `rt.lab_hosting`: an HTTP request only commits a
request and an operation - it never allocates, launches or waits on an engine.

Replaces `LabControl.validate`'s synchronous stand-in (its `SmokeTest` port, `NoEngine` 503 in
the composed Lab) for the new deployments; no second registry, catalog or access check: rows
are 0007's, access is L2's `LabAccess.require`, the artifact is AP-04's verified manifest.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from ...contracts import api, errors
from ...contracts.api import FieldError, Wire
from ...contracts.v2.records import (DeploymentRevision, DeploymentState, Environment,
                                     ProviderCapability, ServingRevision, Visibility)
from ...state.control_ops import ControlOps, input_hash
from ..access import LabAccess
from ..artifacts import verify
from ..artifacts.projects import Unsupported
from ..artifacts.store import Artifact, ArtifactStore, Revision
from ..control import LabControl
from .store import Allocation, HostingStore, Receipt

C, S = ProviderCapability, DeploymentState
KINDS = ("deployment.create", "deployment.smoke", "deployment.retire")

# serve.sh's pinned launch (models/marlin2b/serving-version.json `flags` with its `settings`):
# options_digest(FLAGS) is the profile's engine_options_digest (tests/ap05 pins both).
FLAGS = ("--served-model-name", "marlin2b",
         "--hf-overrides", '{"architectures":["Qwen3_5ForConditionalGeneration"]}',
         "--max-model-len", "32768", "--max-num-seqs", "8",
         "--gpu-memory-utilization", "0.90", "--limit-mm-per-prompt", '{"video":1,"image":4}',
         "--dtype", "bfloat16", "--allowed-local-media-path", "/opt/dlami/nvme/processing")


class HostingProfile(Wire):
    """One approved runtime + image + GPU shape. `recommended` needs measured evidence: none
    is claimed. `availability` says whether this deployment has a hosting target at all."""

    profile_id: str
    runtime: str = "vllm"
    runtime_image_ref: str
    engine_options_digest: str
    flags: tuple[str, ...]
    architecture: str
    precision: str
    gpu: str
    gpu_count: int
    served_model_name: str
    prompt_harness_ref: str
    preprocessor_profile_version: str
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    max_model_len: int
    max_replicas: int
    warm_policies: tuple[str, ...]
    recommended: bool = False
    evidence: str
    availability: api.Availability = api.Availability(state="unknown")


PROFILE = HostingProfile(
    profile_id=verify.PROFILE_ID, runtime_image_ref=verify.RUNTIME_IMAGE,
    engine_options_digest=verify.ENGINE_OPTIONS_DIGEST, flags=FLAGS,
    architecture=verify.ARCHITECTURES["MarlinForConditionalGeneration"],
    precision=verify.PRECISION, gpu=verify.HARDWARE, gpu_count=1, served_model_name="marlin2b",
    prompt_harness_ref="marlin2b.chat.v1", preprocessor_profile_version="marlin2b.video.v1",
    input_modalities=("text", "video"), output_modalities=("text",), max_model_len=32768,
    max_replicas=1, warm_policies=("always_on",),
    evidence="models/marlin2b/serving-version.json (W3, measured 2026-09-23 on the pilot L40S)")


@dataclasses.dataclass(frozen=True)
class Target:
    """The one host slot this deployment of the platform may place a candidate on: its port,
    where installed models live, where verified artifact bytes are read from, and the
    finite clip the smoke sends. ponytail: one slot, one source directory; a pool and an
    object-store source when a second host or uploaded-weights hosting exists."""

    slot: str
    port: int
    model_root: Path
    source_dir: Path
    smoke_video: Path


# ===================================================================== wire bodies ===
class DeploymentRequest(Wire):
    serving_version_id: str = Field(max_length=64)
    hosting_profile: str = Field(default=PROFILE.profile_id, max_length=64)
    endpoint_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    warm_policy: Literal["always_on"] = "always_on"      # no scale-to-zero is claimed
    max_replicas: Literal[1] = 1                          # no autoscaling is claimed
    # the explicit expiry policy: the controller retires it then (1 h .. 7 d), which also
    # bounds its GPU time (one GPU x this) - the approved resource budget of the request
    expire_after_s: int = Field(ge=3600, le=604800)


class AllocationView(Wire):
    state: Literal["reserved", "launched", "released"]
    slot: str
    reserved_at: datetime
    launched_at: datetime | None = None
    released_at: datetime | None = None


class DeploymentDoc(Wire):
    deployment_revision_id: str
    provider_org_id: str
    serving_version_id: str
    endpoint_id: str
    hosting_profile: str
    state: str                                  # the domain state (0007), not the operation's
    request: dict
    allocation: AllocationView | None
    operations: tuple[str, ...]                 # unfinished operations on it, oldest first
    ready: bool
    actions: tuple[str, ...]                    # what the caller may start now
    created_at: datetime
    expires_at: datetime


class Check(Wire):
    kind: str
    passed: bool
    reasons: tuple[FieldError, ...]
    observed: dict
    operation_id: str | None
    checked_at: datetime
    expires_at: datetime


class ReadinessDoc(Wire):
    deployment_revision_id: str
    serving_version_id: str
    state: str
    ready: bool
    reasons: tuple[str, ...]
    identity: Check | None = None
    smoke: Check | None = None
    health: Check | None = None
    checked_at: datetime


# ===================================================================== readiness ===
def latest(receipts: list[Receipt], allocation: Allocation | None) -> dict[str, Receipt]:
    """The newest receipt of each kind for this allocation (`receipts` newest first)."""
    found: dict[str, Receipt] = {}
    for r in receipts:
        if allocation is not None and r.allocation_id == allocation.allocation_id:
            found.setdefault(r.kind, r)
    return found


def gaps(allocation: Allocation | None, found: dict[str, Receipt], now: datetime,
         kinds: tuple[str, ...]) -> list[str]:
    """Why these checks do not hold now for this exact allocation: [] when they all do."""
    if allocation is None:
        return ["not_launched"]
    reasons = []
    for kind in kinds:
        r = found.get(kind)
        if r is None:
            reasons.append(f"{kind}_missing")
        elif not r.passed:
            reasons.append(f"{kind}_failed")
        elif r.expires_at <= now:
            reasons.append(f"{kind}_expired")
    return reasons


def _check(r: Receipt | None) -> Check | None:
    return None if r is None else Check(
        kind=r.kind, passed=r.passed, reasons=r.reasons, observed=r.observed,
        operation_id=r.operation_id, checked_at=r.checked_at, expires_at=r.expires_at)


# ======================================================================= service ===
class LabHosting:
    def __init__(self, access: LabAccess, control: LabControl, artifacts: ArtifactStore,
                 ops: ControlOps, store: HostingStore, target: Target | None) -> None:
        self.access, self.control, self.artifacts = access, control, artifacts
        self.ops, self.store, self.target = ops, store, target

    async def _member(self, actor: api.Actor, capability: C) -> str:
        if actor.provider_org_id is None or actor.user_id is None:
            raise errors.Forbidden("a provider workspace session is required")
        await self.access.require(actor.user_id, actor.provider_org_id, capability)
        return actor.provider_org_id

    async def _mine(self, actor: api.Actor, deployment_id: str, capability: C):
        provider = await self._member(actor, capability)
        hosting = await self.store.hosting(deployment_id)
        if hosting is None or hosting.provider_org_id != provider:
            raise errors.NotFound("no such deployment in this provider workspace")
        deployment = await self.control.store.deployment(deployment_id)
        assert deployment is not None                  # 0062's foreign key
        return hosting, deployment

    # --- reads ------------------------------------------------------------------------
    async def profiles(self, actor: api.Actor) -> api.ListPage:
        await self._member(actor, C.read_aggregate_health)
        state = "configured" if self.target is not None else "unavailable"
        return api.ListPage(data=(PROFILE.model_copy(update={"availability": api.Availability(
            state=state, reason=None if self.target else "no_hosting_target")}),))

    async def get(self, actor: api.Actor, deployment_id: str) -> DeploymentDoc:
        hosting, deployment = await self._mine(actor, deployment_id, C.read_aggregate_health)
        allocation = await self.store.allocation(deployment_id)
        active = await self.store.active(deployment_id)
        ready = (await self.status(deployment_id)).ready
        actions = (("smoke",) if deployment.state is S.validating and not active else ()) + \
            (("retire",) if deployment.state is not S.retired else ())
        return DeploymentDoc(
            deployment_revision_id=deployment_id, provider_org_id=hosting.provider_org_id,
            serving_version_id=hosting.serving_version_id, endpoint_id=hosting.endpoint_id,
            hosting_profile=hosting.profile_id, state=deployment.state.value,
            request=hosting.request, allocation=None if allocation is None else AllocationView(
                **allocation.model_dump(include=set(AllocationView.model_fields))),
            operations=active, ready=ready, actions=actions, created_at=hosting.created_at,
            expires_at=hosting.expires_at)

    async def readiness(self, actor: api.Actor, deployment_id: str) -> ReadinessDoc:
        """The caller's own deployment's readiness (`status`)."""
        await self._mine(actor, deployment_id, C.read_aggregate_health)
        return await self.status(deployment_id)

    async def status(self, deployment_id: str) -> ReadinessDoc:
        """Ready = the revision is `ready_private`, unexpired, and THIS allocation's newest
        identity, smoke and health checks all passed and are current. No access check: the
        server-side read other services take (AP-06's publication gate) - a route asks
        `readiness`."""
        hosting = await self.store.hosting(deployment_id)
        if hosting is None:
            raise errors.NotFound("no such deployment")
        deployment = await self.control.store.deployment(deployment_id)
        assert deployment is not None                  # 0062's foreign key
        allocation = await self.store.allocation(deployment_id)
        now = await self.store.db_now()
        found = latest(await self.store.receipts(deployment_id), allocation)
        reasons = gaps(allocation, found, now, ("identity", "smoke", "health"))
        if deployment.state is not S.ready_private:
            reasons.insert(0, f"state_{deployment.state.value}")
        if hosting.expires_at <= now:
            reasons.append("expired")
        return ReadinessDoc(
            deployment_revision_id=deployment_id, serving_version_id=hosting.serving_version_id,
            state=deployment.state.value, ready=not reasons, reasons=tuple(reasons),
            identity=_check(found.get("identity")), smoke=_check(found.get("smoke")),
            health=_check(found.get("health")), checked_at=now)

    # --- mutations --------------------------------------------------------------------
    async def deploy(self, actor: api.Actor, body: DeploymentRequest,
                     key: str) -> api.OperationDoc:
        """A private draft revision of the provider's own serving revision on its dev
        endpoint, and the `deployment.create` operation that hosts it - one transaction; the
        same key and body answer the same operation."""
        provider = await self._member(actor, C.manage_dev_deployment)
        if self.target is None:
            raise errors.DependencyUnavailable("no hosting target is configured")
        serving = await self.control.catalog.serving_revision(body.serving_version_id)
        if serving is None or serving.provider_org_id != provider:
            raise errors.NotFound("no such serving revision in this provider workspace")
        reasons = await self.unsupported(serving, body)
        if reasons:
            raise Unsupported(reasons)
        endpoint_id = await self.control.store.endpoint(provider, body.endpoint_name,
                                                        Environment.dev,
                                                        actor=actor.user_id or "")
        draft = DeploymentRevision(
            deployment_revision_id=str(uuid.uuid4()), endpoint_id=endpoint_id,
            provider_org_id=provider, serving_version_id=serving.serving_version_id,
            environment=Environment.dev, visibility=Visibility.private, state=S.draft,
            max_input_tokens=body.max_input_tokens, max_output_tokens=body.max_output_tokens,
            created_at=await self.store.db_now())
        started = await self.store.request(
            draft, profile_id=body.hosting_profile, expire_after_s=body.expire_after_s,
            request=body.model_dump(mode="json"), actor=actor, key=key,
            input_hash=input_hash(body.model_dump(mode="json")))
        return started.operation.doc()

    async def unsupported(self, serving: ServingRevision,
                          body: DeploymentRequest) -> list[FieldError]:
        """Every specific reason the request does not fit the one approved profile."""
        def r(field: str, code: str, message: str) -> FieldError:
            return FieldError(field=field, code=code, message=message)
        if body.hosting_profile != PROFILE.profile_id:
            return [r("hosting_profile", "unsupported_profile",
                      "the one approved hosting profile is " + PROFILE.profile_id)]
        reasons = []
        if body.max_input_tokens + body.max_output_tokens > PROFILE.max_model_len:
            reasons.append(r("max_input_tokens", "exceeds_profile",
                             f"input + output tokens exceed {PROFILE.max_model_len}"))
        pins = {"runtime_image_ref": (serving.runtime_image_ref, PROFILE.runtime_image_ref),
                "engine_options_digest": (serving.engine_options_digest,
                                          PROFILE.engine_options_digest),
                "precision": (serving.precision, PROFILE.precision),
                "prompt_harness_ref": (serving.prompt_harness_ref, PROFILE.prompt_harness_ref),
                "preprocessor_profile_version": (serving.preprocessor_profile_version,
                                                 PROFILE.preprocessor_profile_version)}
        reasons += [r(f"serving.{name}", "unsupported_by_profile",
                      "the serving revision does not pin the profile's value")
                    for name, (have, want) in pins.items() if have != want]
        if await self.artifact(serving) is None:
            reasons.append(r("serving_version_id", "no_verified_artifact",
                             "no verified artifact (AP-04) backs this serving revision"))
        return reasons

    async def artifact(self, serving: ServingRevision) -> Artifact | None:
        """The verified artifact AP-04 built this serving revision from (0061's link)."""
        link = await self.artifacts.get(Revision, serving.provider_org_id,
                                        serving.serving_version_id)
        if link is None:
            return None
        return await self.artifacts.get(Artifact, serving.provider_org_id, link.artifact_id)

    async def smoke(self, actor: api.Actor, deployment_id: str, key: str) -> api.OperationDoc:
        """A bounded real smoke of a validating deployment (its controller re-checks the
        identity, sends one finite-video request, records the receipt, and promotes it to
        ready_private only on a pass). ponytail: the state is checked before the key, so a
        retry after the deployment moved on is a 409 naming it, not a replay; read the
        deployment's readiness for that smoke's receipt."""
        _, deployment = await self._mine(actor, deployment_id, C.manage_dev_deployment)
        if deployment.state is not S.validating:
            raise errors.StateConflict(f"a smoke needs a validating deployment; it is "
                                       f"{deployment.state.value}")
        return await self._start("deployment.smoke", actor, deployment_id, key)

    async def retire(self, actor: api.Actor, deployment_id: str, key: str) -> api.OperationDoc:
        """Drain and tear down: in any state (a retired one answers at once)."""
        await self._mine(actor, deployment_id, C.manage_dev_deployment)
        return await self._start("deployment.retire", actor, deployment_id, key)

    async def _start(self, kind: str, actor: api.Actor, deployment_id: str,
                     key: str) -> api.OperationDoc:
        started = await self.ops.start(kind, actor, key, input_hash({kind: deployment_id}),
                                       resource_kind="deployment", resource_id=deployment_id)
        return started.operation.doc()

    async def cancel(self, actor: api.Actor, operation_id: str) -> api.OperationDoc:
        """Stop new work: a queued operation is cancelled, a running one reconciled by its
        controller (0060); another workspace's is not found."""
        await self._member(actor, C.manage_dev_deployment)
        return (await self.ops.cancel(operation_id, actor)).doc()


__all__ = ["FLAGS", "KINDS", "PROFILE", "DeploymentRequest", "HostingProfile", "LabHosting",
           "Target"]
