"""AP-10 10e (R270): reviewed labels -> the supported external training -> a candidate only
through AP-04's import -> the release evidence record, over `infrx.lab.improve.release`.

    POST /lab/v1/providers/{provider}/training-runs/{run_id}/export
         202 + Location: the `training.export` operation (P3's bundle of P1's reviewed labels)
    POST .../training-runs/{run_id}/checkpoints/{checkpoint_id}/candidate
         202 + Location: AP-04's `artifact.import` operation of the approved checkpoint
    GET  .../training-runs/{run_id}/checkpoints/{checkpoint_id}/release-evidence
         200 the write-once record (written by the first read after AP-04 verified the import)

The actor is `rt.actors`' scoped to the path's workspace (`lab_datasets.scoped`: a web session
claims it, a credential of another workspace is 404); the mutations need a current developer+
member (`acting_provider`), the record read any current member. Every mutation takes
`Idempotency-Key`; every failure is the R270 envelope (an `Unsupported` names its fields).

The export is short and write-once (P3's `prepare`), so it runs inside the request under
0060's lease: the operation is finished `succeeded`, or `failed` with the refusal the response
also carries (a replay answers the stored operation). A transient failure finishes nothing:
the lease lapses and the same key runs it again.

Mounted only where the composition put a `LabImprove` on `rt.lab_improve` (with AP-04's
LAB_ARTIFACTS: its import is the candidate's only door; off by default).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, FastAPI, Path, Request
from pydantic import Field

from ...contracts import api, errors
from ...contracts.ids import UUID_RE
from ...contracts.lab import records as lab
from ...contracts.v2.records import ProviderCapability
from ...datasets import acting_provider
from ...judge.submit import require_own_payer
from ...lab.artifacts.imports import ImportSource
from ...lab.artifacts.manifest import FileEntry
from ...lab.artifacts.projects import Unsupported
from ...lab.improve import release
from ...pipelines import training
from ...state.control_ops import input_hash
from .. import control
from .lab_artifacts import ERRORS, EnvelopeRoute, IdempotencyKey, fail
from .lab_datasets import scoped

if TYPE_CHECKING:
    from ...lab.access import LabAccess
    from ...lab.artifacts import LabArtifacts
    from ...state.control_ops import ControlOps

PREFIX = "/lab/v1/providers/{provider}/training-runs/{run_id}"
CHECKPOINT = PREFIX + "/checkpoints/{checkpoint_id}"
KIND = "training.export"
LEASE_S = 60
USD = r"^(0|[1-9][0-9]{0,11})\.[0-9]{8}$"   # a Lab Amount value (R159), PROVIDER_USD
#: path ids name object keys (`lab/<provider>/training/<run>/...`): a UUID, nothing else
Id = Annotated[str, Path(pattern=f"^{UUID_RE.pattern}$")]


@dataclass(frozen=True)
class LabImprove:
    access: LabAccess
    store: object                 # D7: PgLabDataStore (P3's records, the checkpoint receipts)
    objects: object               # the Lab objects (bundles, exports, descriptors, evidence)
    ledger: object                # D8: P3's run ledger and notes
    ops: ControlOps               # 0060: the export operation
    artifacts: LabArtifacts       # AP-04: the candidate's import


class TrainingExport(api.Wire):
    """Any trainer name is read: one other than P3's manual bundle is refused by name."""

    trainer: str = Field(min_length=1, max_length=64)
    dataset_ref: lab.RefOf("dataset")  # type: ignore[valid-type]
    label_export_id: lab.Uuid
    config: training.TrainingConfig
    payer_ref: lab.RefOf("payer")  # type: ignore[valid-type]
    limit: str = Field(pattern=USD)


class CandidateRequest(api.Wire):
    """AP-04's import body for the checkpoint's weights, beside the received descriptor."""

    artifact_key: str = Field(min_length=1, max_length=512)
    project_id: str = Field(max_length=64)
    source: ImportSource
    files: tuple[FileEntry, ...] = Field(min_length=1)
    secret_ref: str | None = Field(default=None, max_length=210)


class ReleaseEvidence(api.Wire):
    format: str
    provider_org_id: str
    external_run_id: str
    external_run_ref: str
    dataset_ref: str
    export: dict[str, Any]
    label_methods: dict[str, int]
    holdout: dict[str, Any]
    config: dict[str, Any]
    checkpoint: dict[str, Any]
    evaluation: Any
    approved_by: Any
    artifact: dict[str, Any]
    qualification: dict[str, Any]
    recorded_at: str


def _failure(exc: errors.DomainError, operation_id: str) -> api.ErrorBody:
    body = api.envelope(exc, operation_id, operation_id=operation_id).error
    if isinstance(exc, Unsupported):
        body = body.model_copy(update={"field_errors": exc.reasons})
    return body


def register(app: FastAPI, rt: Any) -> LabImprove | None:
    x: LabImprove | None = getattr(rt, "lab_improve", None)
    if x is None:
        return None
    actors: control.ActorSource | None = getattr(rt, "actors", None)
    router = APIRouter(route_class=EnvelopeRoute, responses=ERRORS)

    async def handle(request: Request, provider: str,
                     work: Callable[[api.Actor], Awaitable[Any]]) -> Any:
        try:
            if actors is None:
                raise errors.DependencyUnavailable("the Lab session actors are not composed")
            return await work(scoped(await actors.actor(request), provider))
        except Exception as exc:    # noqa: BLE001 - every failure is the envelope, never a 500 page
            return fail(exc, request)

    @router.post(PREFIX + "/export", status_code=202, response_model=api.OperationDoc,
                 operation_id="startTrainingExport")
    async def export(request: Request, provider: str, run_id: Id, body: TrainingExport,
                     key: IdempotencyKey):
        async def work(actor: api.Actor):
            await acting_provider(x.access, actor.user_id or "", provider)
            require_own_payer(provider, body.payer_ref)
            op = (await x.ops.start(KIND, actor, key, input_hash(
                {"run_id": run_id, **body.model_dump(mode="json")}),
                resource_kind="external_run", resource_id=run_id)).operation
            try:
                op = await x.ops.lease(op.operation_id, str(uuid.uuid4()), LEASE_S)
            except errors.Conflict:   # finished, or another request holds it: as it stands
                return control.accepted(op.doc(), f"/lab/v1/operations/{op.operation_id}")
            try:
                await release.export_for_training(
                    x.store, x.objects, x.ledger, trainer=body.trainer,
                    provider_org_id=provider, actor=actor.user_id or "", external_run_id=run_id,
                    dataset_ref=body.dataset_ref, config=body.config.model_dump(),
                    label_export_id=body.label_export_id, payer_ref=body.payer_ref,
                    limit=body.limit, now=await x.access.store.db_now())
            except (errors.ServerError, errors.RateLimitError):
                raise                             # the lease lapses; the same key runs it again
            except errors.DomainError as refused:
                await x.ops.finish(op.operation_id, op.fence, "failed",
                                   _failure(refused, op.operation_id))
                raise
            op = await x.ops.finish(op.operation_id, op.fence, "succeeded")
            return control.accepted(op.doc(), f"/lab/v1/operations/{op.operation_id}")
        return await handle(request, provider, work)

    @router.post(CHECKPOINT + "/candidate", status_code=202, response_model=api.OperationDoc,
                 operation_id="registerTrainingCandidate")
    async def candidate(request: Request, provider: str, run_id: Id, checkpoint_id: Id,
                        body: CandidateRequest, key: IdempotencyKey):
        async def work(actor: api.Actor):
            await acting_provider(x.access, actor.user_id or "", provider)
            op = await release.register_candidate(
                x.store, x.objects, x.ledger, x.artifacts, actor, key,
                provider_org_id=provider, external_run_id=run_id, checkpoint_id=checkpoint_id,
                artifact_key=body.artifact_key, project_id=body.project_id,
                source=body.source.model_dump(mode="json"),
                files=[f.model_dump(mode="json") for f in body.files],
                secret_ref=body.secret_ref)
            return control.accepted(op.doc, f"/lab/v1/operations/{op.doc.operation_id}")
        return await handle(request, provider, work)

    @router.get(CHECKPOINT + "/release-evidence", response_model=ReleaseEvidence,
                operation_id="getReleaseEvidence")
    async def evidence(request: Request, provider: str, run_id: Id, checkpoint_id: Id):
        async def work(actor: api.Actor):
            await x.access.require(actor.user_id or "", provider,
                                   ProviderCapability.read_aggregate_health)
            return control.ok(ReleaseEvidence.model_validate(await release.release_evidence(
                x.objects, x.ledger, x.artifacts, provider_org_id=provider,
                external_run_id=run_id, checkpoint_id=checkpoint_id,
                now=await x.access.store.db_now())))
        return await handle(request, provider, work)

    app.include_router(router)
    return x


__all__ = ["CandidateRequest", "LabImprove", "ReleaseEvidence", "TrainingExport", "register"]
