"""AP-04 (R270): artifact intake, artifacts, operations and the operator's adoption.

    POST /lab/v1/artifacts/uploads                     201 the upload session
    POST /lab/v1/artifacts/uploads/{id}/parts          200 a presigned PUT for one declared path
    POST /lab/v1/artifacts/uploads/{id}/complete       202 + Location: the verification operation
    POST /lab/v1/artifacts/imports                     202 + Location: the import operation
    GET  /lab/v1/artifacts/{id}                        200 the verified artifact
    GET  /lab/v1/operations/{id}                       200 an operation of the caller's workspace
    POST /operator/v1/artifacts/adopt                  201 the adopted artifact (operator)

The actor is `rt.actors`' only; every mutation takes `Idempotency-Key`; every failure is the
R270 envelope (`Unsupported` adds its reasons as `field_errors`; a body FastAPI refuses is a
422 envelope too, via `EnvelopeRoute`). Nothing mounts while `rt.lab_artifacts` is None.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ...contracts import api, errors
from ...contracts.v2.records import ProviderCapability
from ...lab.artifacts.imports import ImportRequest
from ...lab.artifacts.projects import AdoptRequest, Unsupported
from ...lab.artifacts.store import Artifact, Upload
from ...lab.artifacts.uploads import CompleteRequest, PartGrant, PartRequest, UploadRequest
from .. import control

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": api.ErrorEnvelope} for status in (401, 403, 404, 409, 410, 413, 422, 503)}


def fail(exc: BaseException, request: Request) -> JSONResponse:
    """`control.error_response`, plus an `Unsupported` refusal's specific reasons."""
    rid = control.request_id(request)
    if not isinstance(exc, Unsupported):
        return control.error_response(exc, rid)
    body = api.envelope(exc, rid)
    body = body.model_copy(update={"error": body.error.model_copy(
        update={"field_errors": exc.reasons})})
    return JSONResponse(body.model_dump(mode="json"), status_code=422, headers=control.NO_STORE)


class EnvelopeRoute(APIRoute):
    """A body or parameter FastAPI refuses is R270's 422, naming fields, never echoing input."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Any]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Any:
            try:
                return await handler(request)
            except RequestValidationError as refused:
                exc = Unsupported([api.FieldError(
                    field=".".join(str(p) for p in e["loc"]), code=e["type"],
                    message=str(e["msg"])[:300]) for e in refused.errors()])
                return fail(exc, request)
        return run


async def guarded(request: Request, rt: Any, work: Callable[[api.Actor], Awaitable[Any]],
                  ) -> Any:
    try:
        return await work(await rt.actors.actor(request))
    except Exception as exc:        # noqa: BLE001 - every failure is the envelope, never a 500 page
        return fail(exc, request)


def accepted(op: Any) -> JSONResponse:
    return control.accepted(op.doc, f"/lab/v1/operations/{op.doc.operation_id}")


def register(app: FastAPI, rt: Any) -> None:
    a = getattr(rt, "lab_artifacts", None)
    if a is None:
        return
    router = APIRouter(route_class=EnvelopeRoute, responses=ERRORS)

    @router.post("/lab/v1/artifacts/uploads", status_code=201, response_model=Upload,
                 operation_id="createArtifactUpload")
    async def create_upload(request: Request, body: UploadRequest, key: IdempotencyKey):
        return await guarded(request, rt, lambda actor: _ok(
            a.uploads.create(actor, body, key), 201))

    @router.post("/lab/v1/artifacts/uploads/{upload_id}/parts", response_model=PartGrant,
                 operation_id="grantArtifactUploadPart")
    async def grant_part(request: Request, upload_id: str, body: PartRequest):
        return await guarded(request, rt, lambda actor: _ok(
            a.uploads.part(actor, upload_id, body)))

    @router.post("/lab/v1/artifacts/uploads/{upload_id}/complete", status_code=202,
                 response_model=api.OperationDoc, operation_id="completeArtifactUpload")
    async def complete_upload(request: Request, upload_id: str, body: CompleteRequest,
                              key: IdempotencyKey):
        async def work(actor):
            return accepted(await a.uploads.complete(actor, upload_id, body, key))
        return await guarded(request, rt, work)

    @router.post("/lab/v1/artifacts/imports", status_code=202, response_model=api.OperationDoc,
                 operation_id="startArtifactImport")
    async def start_import(request: Request, body: ImportRequest, key: IdempotencyKey):
        async def work(actor):
            return accepted(await a.imports.start(actor, body, key))
        return await guarded(request, rt, work)

    @router.get("/lab/v1/artifacts/{artifact_id}", response_model=Artifact,
                operation_id="getArtifact")
    async def get_artifact(request: Request, artifact_id: str):
        async def work(actor):
            if actor.provider_org_id is None or actor.user_id is None:
                raise errors.Forbidden("a provider workspace session is required")
            await a.projects.access.require(actor.user_id, actor.provider_org_id,
                                            ProviderCapability.read_aggregate_health)
            found = await a.projects.store.get(Artifact, actor.provider_org_id, artifact_id)
            if found is None:
                raise errors.NotFound("no such verified artifact in this provider workspace")
            return control.ok(found)
        return await guarded(request, rt, work)

    @router.get("/lab/v1/operations/{operation_id}", response_model=api.OperationDoc,
                operation_id="getOperation")
    async def get_operation(request: Request, operation_id: str):
        async def work(actor):
            if actor.provider_org_id is None or actor.user_id is None:
                raise errors.Forbidden("a provider workspace session is required")
            await a.projects.access.require(actor.user_id, actor.provider_org_id,
                                            ProviderCapability.read_aggregate_health)
            op = await a.ops.get(operation_id)
            if op is None or op.actor.provider_org_id != actor.provider_org_id:
                raise errors.NotFound("no such operation in this provider workspace")
            return control.ok(op.doc)
        return await guarded(request, rt, work)

    @router.post("/operator/v1/artifacts/adopt", status_code=201, response_model=Artifact,
                 operation_id="adoptArtifact")
    async def adopt(request: Request, body: AdoptRequest, key: IdempotencyKey):
        return await guarded(request, rt, lambda actor: _ok(a.projects.adopt(actor, body), 201))

    app.include_router(router)


async def _ok(result: Awaitable[Any], status_code: int = 200) -> JSONResponse:
    return control.ok(await result, status_code)
