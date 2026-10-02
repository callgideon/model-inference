"""AP-05: the hosting controller - the out-of-process half that holds compute access.

One `run_once` pass: lease every pending deployment operation (0060 `pending`, plus the ones
this controller already holds) and advance it as far as it can go without waiting; then
reconcile the live deployments (health, expiry, abandoned drafts). Every step is idempotent
and resumable: a controller killed at any boundary leaves its lease to expire, and the next
holder (fence + 1) re-reads the allocation, finds the engine by its resource tag instead of
starting a second one, re-uses the receipts this operation already recorded, and finishes
or fails terminally - the old holder's writes are refused by the fence. A create or smoke
older than its deadline fails terminally (never validating forever).

    create   capacity -> install -> launch -> identity -> succeeded (deployment validating)
    smoke    identity -> smoke -> promote -> succeeded (deployment ready_private)
    retire   waiting (other operations cancelled) -> drain -> teardown -> succeeded
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from ...contracts import api, errors
from ...contracts.api import FieldError
from ...contracts.v2.records import DeploymentState, ServingRevision
from ...state.control_ops import Operation, input_hash
from . import KINDS, PROFILE, LabHosting, Target
from .engine import Engine, InstallRefused, Launcher, Runtime, install, measure, mismatches, \
    options_digest
from .store import Allocation, Hold, Receipt, tag

log = logging.getLogger(__name__)
S = DeploymentState
LEASE_S = 60
# est.: vLLM loads Marlin-2B in minutes on the L40S; a candidate silent this long has failed
LAUNCH_TIMEOUT_S = 900
DEADLINE_S = 7200                 # a create or smoke operation ends within this
VALIDATION_WINDOW_S = 3600        # a draft/validating deployment not ready by then is retired
IDENTITY_TTL_S = SMOKE_TTL_S = 86400
HEALTH_EVERY_S, HEALTH_TTL_S = 60, 180


HARNESS = (PROFILE.prompt_harness_ref, PROFILE.preprocessor_profile_version)


def _runtime(runtime: Runtime | None) -> dict:
    """A runtime as a receipt records it (never a secret: image, flags, directory)."""
    if runtime is None:
        return {"running": False}
    return {"running": True, "image": runtime.image, "image_declared": runtime.declared,
            "options_digest": options_digest(runtime.flags), "model_dir": runtime.model_dir}


def system(provider_org_id: str) -> api.Actor:
    """The controller's own actor for the operations it starts (expiry, reconciliation)."""
    return api.Actor(audience="operator", operator=True, provider_org_id=provider_org_id)


class Controller:
    def __init__(self, hosting: LabHosting, launcher: Launcher, target: Target, *,
                 owner: str | None = None, boundary: Callable[[str], None] | None = None,
                 transport=None, lease_s: int = LEASE_S,
                 launch_timeout_s: int = LAUNCH_TIMEOUT_S) -> None:
        self.h, self.launcher, self.target = hosting, launcher, target
        self.store, self.ops, self.control = hosting.store, hosting.ops, hosting.control
        self.owner = owner or f"hosting-{uuid.uuid4()}"
        self.boundary = boundary or (lambda name: None)
        self.engine = Engine(transport)
        self.lease_s, self.launch_timeout_s = lease_s, launch_timeout_s
        self.holding: set[str] = set()

    async def run_once(self) -> int:
        """One pass; the number of operations it finished."""
        pending = await self.ops.pending(KINDS, 100)
        done = 0
        for op_id in [*sorted(self.holding), *(i for i in pending if i not in self.holding)]:
            done += await self._step(op_id)
        await self._reconcile()
        return done

    async def _step(self, op_id: str) -> int:
        try:
            op = await self.ops.lease(op_id, self.owner, self.lease_s)
        except errors.Conflict:                   # finished, or another holder's live lease
            self.holding.discard(op_id)
            return 0
        self.holding.add(op_id)
        hold = Hold(op_id, op.fence, op.resource_id or "")
        work = {"deployment.create": self._create, "deployment.smoke": self._smoke,
                "deployment.retire": self._retire}[op.kind]
        try:
            if op.kind != "deployment.retire" and await self._late(op):
                finished = await self._fail(op, hold, "deadline_exceeded",
                                            "the operation did not finish in time")
            else:
                finished = await work(op, hold)
        except errors.DomainError as refused:     # a lost lease or a race: re-read next pass
            log.warning("hosting %s %s: %s", op.kind, op_id, refused.code)
            self.holding.discard(op_id)
            return 0
        if finished:
            self.holding.discard(op_id)
        return int(finished)

    async def _late(self, op: Operation) -> bool:
        created = datetime.fromisoformat(op.created_at.replace("Z", "+00:00"))
        return await self.store.db_now() - created > timedelta(seconds=DEADLINE_S)

    # --- shared ----------------------------------------------------------------------
    async def _deployment(self, deployment_id: str):
        d = await self.control.store.deployment(deployment_id)
        assert d is not None
        return d

    async def _serving(self, deployment_id: str) -> ServingRevision:
        d = await self._deployment(deployment_id)
        serving = await self.control.catalog.serving_revision(d.serving_version_id)
        assert serving is not None
        return serving

    def _dir(self, allocation: Allocation):
        return self.target.model_root / allocation.resource_tag

    async def _receipt(self, hold: Hold | None, allocation: Allocation, kind: str,
                       observed: dict, reasons: list[FieldError], ttl_s: int) -> Receipt:
        now = await self.store.db_now()
        return await self.store.record(hold, Receipt(
            receipt_id=str(uuid.uuid4()), deployment_revision_id=allocation.deployment_revision_id,
            allocation_id=allocation.allocation_id, kind=kind, passed=not reasons,  # type: ignore[arg-type]
            observed=observed, reasons=tuple(reasons),
            operation_id=hold.operation_id if hold else None, checked_at=now,
            expires_at=now + timedelta(seconds=ttl_s)))

    async def _mine(self, op: Operation, kind: str) -> Receipt | None:
        """The receipt of `kind` this very operation already recorded (a resumed holder
        re-uses it instead of observing or smoking twice)."""
        return next((r for r in await self.store.receipts(op.resource_id or "")
                     if r.operation_id == op.operation_id and r.kind == kind), None)

    async def _retired(self, hold: Hold, reason: str) -> None:
        d = await self._deployment(hold.deployment_revision_id)
        if d.state is not S.retired:
            await self.store.transition(hold, d.deployment_revision_id, d.provider_org_id,
                                        d.state.value, S.retired.value, f"hosting: {reason}")

    async def _teardown(self, hold: Hold) -> None:
        """Stop this deployment's own engine (found by its tag, never by port), remove its
        installed model, release its slot."""
        allocation = await self.store.allocation(hold.deployment_revision_id)
        if allocation is None or allocation.state == "released":
            return
        await self.launcher.stop(allocation)
        self.boundary("stopped")
        await asyncio.to_thread(shutil.rmtree, self._dir(allocation), True)
        await self.store.move(hold, allocation.allocation_id, "released")
        self.boundary("released")

    async def _fail(self, op: Operation, hold: Hold, code: str, message: str, *,
                    retryable: bool = False, keep: bool = False) -> bool:
        """End the operation failed; a create's resources are torn down and its draft or
        validating revision retired (a failed validation never becomes ready or public)."""
        if not keep:
            await self._teardown(hold)
            await self._retired(hold, code)
        await self.ops.finish(op.operation_id, hold.fence, "failed", api.ErrorBody(
            code=code, message=message, request_id=op.operation_id, retryable=retryable,
            operation_id=op.operation_id, resource_id=hold.deployment_revision_id))
        return True

    async def _cancelled(self, op: Operation, hold: Hold) -> bool:
        await self._teardown(hold)
        await self._retired(hold, "cancelled")
        await self.ops.finish(op.operation_id, hold.fence, "cancelled")
        return True

    # --- create ----------------------------------------------------------------------
    async def _create(self, op: Operation, hold: Hold) -> bool:
        deployment_id = hold.deployment_revision_id
        d = await self._deployment(deployment_id)
        if op.state == "cancel_requested" or d.state is S.retired:
            return await self._cancelled(op, hold)
        serving = await self._serving(deployment_id)
        allocation = await self.store.allocation(deployment_id)
        if allocation is None or allocation.state == "released":
            await self.ops.advance(op.operation_id, hold.fence, "capacity")
            try:
                allocation = await self.store.allocate(hold, Allocation(
                    allocation_id=str(uuid.uuid4()), deployment_revision_id=deployment_id,
                    slot=self.target.slot, port=self.target.port, resource_tag=tag(deployment_id),
                    operation_id=op.operation_id, reserved_at=await self.store.db_now()))
            except errors.CapacityUnavailable:
                return await self._fail(op, hold, "capacity_unavailable",
                                        "the hosting slot holds another deployment",
                                        retryable=True)
            self.boundary("allocated")
        if allocation.state == "reserved":
            await self.ops.advance(op.operation_id, hold.fence, "install")
            artifact = await self.h.artifact(serving)
            assert artifact is not None                    # checked when it was requested
            try:
                await asyncio.to_thread(install, self.target.source_dir, artifact.files,
                                        self._dir(allocation))
            except InstallRefused as refused:
                return await self._fail(op, hold, "artifact_unavailable",
                                        f"the verified bytes are not at the source: {refused}")
            self.boundary("installed")
            await self.ops.advance(op.operation_id, hold.fence, "launch")
            if d.state is S.draft:
                await self.store.transition(hold, deployment_id, d.provider_org_id, "draft",
                                            "validating", "hosting: launching")
            if await self.launcher.inspect(allocation) is None:
                await self.launcher.start(allocation, self._dir(allocation))
            self.boundary("launched")
            allocation = await self.store.move(hold, allocation.allocation_id, "launched")
        receipt = await self._mine(op, "identity")
        if receipt is None:
            await self.ops.advance(op.operation_id, hold.fence, "identity", retry_after_s=5)
            if await self.launcher.inspect(allocation) is None:
                return await self._fail(op, hold, "engine_exited",
                                        "the candidate engine is not running")
            models = await self.engine.models(allocation)
            if models is None:
                waited = await self.store.db_now() - (allocation.launched_at or op_now(op))
                if waited > timedelta(seconds=self.launch_timeout_s):
                    return await self._fail(op, hold, "engine_timeout",
                                            "the candidate engine never answered")
                return False                                       # next pass
            receipt = await self._identity(hold, allocation, serving, models)
            self.boundary("identity")
        if not receipt.passed:
            return await self._fail(op, hold, "identity_mismatch",
                                    "the engine does not serve the requested revision")
        await self.ops.finish(op.operation_id, hold.fence, "succeeded")
        return True

    async def _identity(self, hold: Hold, allocation: Allocation, serving: ServingRevision,
                        models: list[str] | None) -> Receipt:
        """Observe the engine (its runtime as the launcher reads it, the model names it
        serves, the installed bytes measured now) and record it against the revision."""
        artifact = await self.h.artifact(serving)
        runtime = await self.launcher.inspect(allocation)
        files = await asyncio.to_thread(measure, self._dir(allocation))
        reasons = mismatches(serving, runtime, models, self._dir(allocation),
                             served_model=PROFILE.served_model_name, harness=HARNESS,
                             files=files, manifest=artifact.files if artifact else ())
        return await self._receipt(hold, allocation, "identity", {
            "serving_version_id": serving.serving_version_id, "files": files,
            "served_models": models, **_runtime(runtime)}, reasons, IDENTITY_TTL_S)

    # --- smoke -------------------------------------------------------------------------
    async def _smoke(self, op: Operation, hold: Hold) -> bool:
        raise errors.InvalidRequest("smoke arrives in 05d")

    # --- retire ------------------------------------------------------------------------
    async def _retire(self, op: Operation, hold: Hold) -> bool:
        deployment_id = hold.deployment_revision_id
        d = await self._deployment(deployment_id)
        if op.state == "cancel_requested" and d.state is not S.retired:
            await self.ops.finish(op.operation_id, hold.fence, "cancelled")
            return True
        others = [o for o in await self.store.active(deployment_id) if o != op.operation_id]
        for other in others:
            await self.ops.cancel(other, system(d.provider_org_id))
        if others:
            await self.ops.advance(op.operation_id, hold.fence, "waiting", retry_after_s=5)
            return False
        await self.ops.advance(op.operation_id, hold.fence, "drain")
        await self._retired(hold, "retired")
        await self.ops.advance(op.operation_id, hold.fence, "teardown")
        await self._teardown(hold)
        await self.ops.finish(op.operation_id, hold.fence, "succeeded")
        return True

    # --- reconciliation ------------------------------------------------------------------
    async def _reconcile(self) -> None:
        now = await self.store.db_now()
        for hosting in await self.store.live():
            deployment_id = hosting.deployment_revision_id
            if await self.store.active(deployment_id):
                continue
            d = await self._deployment(deployment_id)
            window = hosting.created_at + timedelta(seconds=VALIDATION_WINDOW_S)
            if (d.state in (S.draft, S.retired) or now >= hosting.expires_at
                    or (d.state is S.validating and now >= window)):
                await self.ops.start("deployment.retire", system(hosting.provider_org_id),
                                     f"reconcile:{deployment_id}",
                                     input_hash({"retire": deployment_id}),
                                     resource_kind="deployment", resource_id=deployment_id)


def op_now(op: Operation) -> datetime:
    return datetime.fromisoformat(op.updated_at.replace("Z", "+00:00"))
