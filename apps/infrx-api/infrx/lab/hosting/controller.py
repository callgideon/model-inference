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
from pathlib import Path

from ...contracts import api, errors
from ...contracts.api import FieldError
from ...contracts.v2.records import DeploymentState, ServingRevision
from ...state.control_ops import Operation, input_hash
from . import KINDS, PROFILE, LabHosting, Target, gaps, latest
from .engine import (BoxLauncher, Engine, InstallRefused, Launcher, Runtime, install, measure,
                     mismatches, options_digest)
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
DRAIN_S = 300                     # a retire waits this long (from its request) for in-flight work
SMOKE_TIMEOUT_S = 120.0           # one finite clip <= 72 s answers in ~2-14 s measured (c=1..16)


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
                 launch_timeout_s: int = LAUNCH_TIMEOUT_S,
                 smoke_timeout_s: float = SMOKE_TIMEOUT_S) -> None:
        self.h, self.launcher, self.target = hosting, launcher, target
        self.store, self.ops, self.control = hosting.store, hosting.ops, hosting.control
        self.owner = owner or f"hosting-{uuid.uuid4()}"
        self.boundary = boundary or (lambda name: None)
        self.engine = Engine(transport)
        self.lease_s, self.launch_timeout_s = lease_s, launch_timeout_s
        self.smoke_timeout_s = smoke_timeout_s
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
        return await self.store.db_now() - _at(op.created_at) > timedelta(seconds=DEADLINE_S)

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
        """Re-observe the identity, send one bounded finite-video request, and promote to
        ready_private only when THIS operation's identity and smoke receipts are the newest
        of the current allocation, passed and unexpired. A failure keeps the deployment
        validating (smoke again or retire; the validation window bounds it)."""
        deployment_id = hold.deployment_revision_id
        d = await self._deployment(deployment_id)
        smoke = await self._mine(op, "smoke")
        if d.state is S.ready_private and smoke is not None and smoke.passed:
            await self.ops.finish(op.operation_id, hold.fence, "succeeded")   # promoted, then
            return True                                                      # stopped
        if op.state == "cancel_requested":
            await self.ops.finish(op.operation_id, hold.fence, "cancelled")
            return True
        allocation = await self.store.allocation(deployment_id)
        if d.state is not S.validating or allocation is None or allocation.state != "launched":
            return await self._fail(op, hold, "state_conflict", "the deployment is "
                                    f"{d.state.value}, not a launched validating one", keep=True)
        serving = await self._serving(deployment_id)
        identity = await self._mine(op, "identity")
        if identity is None:
            await self.ops.advance(op.operation_id, hold.fence, "identity")
            identity = await self._identity(hold, allocation, serving,
                                            await self.engine.models(allocation))
            self.boundary("identity")
        if not identity.passed:
            return await self._fail(op, hold, "identity_mismatch",
                                    "the engine does not serve the requested revision", keep=True)
        if smoke is None:
            await self.ops.advance(op.operation_id, hold.fence, "smoke")
            video = await asyncio.to_thread(self.target.smoke_video.read_bytes)
            observed, reasons = await self.engine.smoke(
                allocation, model=PROFILE.served_model_name, video=video,
                timeout_s=self.smoke_timeout_s)
            smoke = await self._receipt(hold, allocation, "smoke", observed, reasons,
                                        SMOKE_TTL_S)
            self.boundary("smoke")
        if not smoke.passed:
            return await self._fail(op, hold, "smoke_failed",
                                    "the smoke did not get a served video answer", keep=True)
        current = latest(await self.store.receipts(deployment_id), allocation)
        if gaps(allocation, current, await self.store.db_now(), ("identity", "smoke")) or any(
                current[kind].operation_id != op.operation_id for kind in ("identity", "smoke")):
            return await self._fail(op, hold, "stale_receipt",
                                    "a newer or expired check supersedes this smoke", keep=True)
        await self.ops.advance(op.operation_id, hold.fence, "promote")
        await self.store.transition(hold, deployment_id, d.provider_org_id, "validating",
                                    "ready_private", "hosting: identity and smoke passed")
        self.boundary("promoted")
        await self.ops.finish(op.operation_id, hold.fence, "succeeded")
        return True

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
        await self.ops.advance(op.operation_id, hold.fence, "drain", retry_after_s=2)
        await self._retired(hold, "retired")                   # nothing new resolves to it
        self.boundary("retired")
        allocation = await self.store.allocation(deployment_id)
        if (allocation is not None and allocation.state == "launched"
                and await self.engine.in_flight(allocation)
                and await self.store.db_now() < _at(op.created_at)
                + timedelta(seconds=DRAIN_S)):
            return False                       # its in-flight requests finish first (bounded)
        await self.ops.advance(op.operation_id, hold.fence, "teardown")
        await self._teardown(hold)
        await self.ops.finish(op.operation_id, hold.fence, "succeeded")
        return True

    # --- reconciliation ------------------------------------------------------------------
    async def _reconcile(self) -> None:
        """Every live deployment no operation holds: retire it when it expired, was left a
        draft, stayed validating past its window, or is retired with resources left; else
        observe a launched engine's health (runtime identity + served name) every minute."""
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
                continue
            allocation = await self.store.allocation(deployment_id)
            if allocation is None or allocation.state != "launched":
                continue
            health = latest(await self.store.receipts(deployment_id), allocation).get("health")
            if health is None or now >= health.checked_at + timedelta(seconds=HEALTH_EVERY_S):
                await self._health(allocation, await self._serving(deployment_id))

    async def _health(self, allocation: Allocation, serving: ServingRevision) -> Receipt:
        runtime = await self.launcher.inspect(allocation)
        models = await self.engine.models(allocation)
        reasons = mismatches(serving, runtime, models, self._dir(allocation),
                             served_model=PROFILE.served_model_name, harness=HARNESS)
        return await self._receipt(None, allocation, "health", {
            "served_models": models, **_runtime(runtime)}, reasons, HEALTH_TTL_S)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def op_now(op: Operation) -> datetime:
    return _at(op.updated_at)


# --- the `hosting` worker role (WR-AP05-3: `python -m infrx.lab.workers hosting`) -----------
PASS_S = 2.0
NEEDS = ("HOSTING_SLOT", "HOSTING_PORT", "HOSTING_MODEL_ROOT", "HOSTING_SOURCE_DIR",
         "HOSTING_SMOKE_VIDEO")


def target_from(env) -> Target | None:
    """The configured slot (infra/lab/hosting/README.md step 3), or None: this deployment of
    the platform hosts nothing and the profile reads `unavailable`. A candidate port is
    8100-8199: never the serving engine's 8000."""
    if not env.get("HOSTING_SLOT"):
        return None
    port = env["HOSTING_PORT"]
    if not (port.isdigit() and 8100 <= int(port) <= 8199):
        raise ValueError("HOSTING_PORT must be a candidate port 8100-8199")
    return Target(slot=env["HOSTING_SLOT"], port=int(port),
                  model_root=Path(env["HOSTING_MODEL_ROOT"]),
                  source_dir=Path(env["HOSTING_SOURCE_DIR"]),
                  smoke_video=Path(env["HOSTING_SMOKE_VIDEO"]))


def launcher_from(env) -> Launcher:
    """The one approved host's launcher (LocalLauncher is the isolated proofs' only)."""
    return BoxLauncher(Path(env.get("HOSTING_ENV_DIR", "/etc/infrx-lab/hosting")))


def tasks(env, connect, *, owner: str) -> dict:
    """The role's one pass over the Lab database (the control login's grants: 0060-0062)."""
    from ...state.control_ops import PgControlOps
    from ...state.lab_access import PgAccessStore
    from ...worker.__main__ import every
    from ..access import LabAccess
    from ..artifacts.store import PgArtifactStore
    from ..compose import lab_control
    from .store import PgHostingStore
    target = target_from(env)
    if target is None:
        raise ValueError("HOSTING_SLOT: the hosting role needs its slot")
    access = LabAccess(PgAccessStore(connect))
    hosting = LabHosting(access, lab_control(connect, access), PgArtifactStore(connect),
                         PgControlOps(connect), PgHostingStore(connect), target)
    controller = Controller(hosting, launcher_from(env), target, owner=owner)
    return {"hosting": lambda: every(PASS_S, controller.run_once, "hosting pass")}
