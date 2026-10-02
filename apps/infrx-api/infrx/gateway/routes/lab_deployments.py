"""AP-05 (R270): private deployments and their operations.

    GET  /lab/v1/hosting-profiles                          {data, next_cursor} the approved profile
    POST /lab/v1/control/deployments                       202 + Location: deployment.create
    GET  /lab/v1/control/deployments/{id}                  200 the deployment (domain state,
                                                           allocation, operations, actions)
    GET  /lab/v1/control/deployments/{id}/readiness        200 ready + every check's receipt
    POST /lab/v1/control/deployments/{id}/smoke            202 + Location: deployment.smoke
    POST /lab/v1/control/deployments/{id}/retire           202 + Location: deployment.retire
    POST /lab/v1/operations/{id}/cancel                    200 the operation (cancel requested)

The rendering, actor and refusal rules are `lab_artifacts`'s (one `EnvelopeRoute`, one
`guarded`, `Unsupported` reasons as `field_errors`); every mutation but the cancel takes an
`Idempotency-Key` (a cancel is idempotent by state: repeated, it answers the operation as it
is). The `Location` is AP-04's `GET /lab/v1/operations/{id}`. Nothing mounts while
`rt.lab_hosting` is None.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request

from ...contracts import api
from ...lab.hosting import DeploymentDoc, DeploymentRequest, HostingProfile, ReadinessDoc
from .. import control
from .lab_artifacts import ERRORS, EnvelopeRoute, IdempotencyKey, guarded


def accepted(doc: api.OperationDoc):
    return control.accepted(doc, f"/lab/v1/operations/{doc.operation_id}")


def register(app: FastAPI, rt: Any) -> None:
    h = getattr(rt, "lab_hosting", None)
    if h is None:
        return
    router = APIRouter(route_class=EnvelopeRoute, responses=ERRORS)
    base = "/lab/v1/control/deployments"

    @router.get("/lab/v1/hosting-profiles", response_model=api.ListPage[HostingProfile],
                operation_id="listHostingProfiles")
    async def profiles(request: Request):
        async def work(actor):
            return control.ok(await h.profiles(actor))
        return await guarded(request, rt, work)

    @router.post(base, status_code=202, response_model=api.OperationDoc,
                 operation_id="createDeployment")
    async def deploy(request: Request, body: DeploymentRequest, key: IdempotencyKey):
        async def work(actor):
            return accepted(await h.deploy(actor, body, key))
        return await guarded(request, rt, work)

    @router.get(base + "/{deployment_id}", response_model=DeploymentDoc,
                operation_id="getDeployment")
    async def detail(request: Request, deployment_id: str):
        async def work(actor):
            return control.ok(await h.get(actor, deployment_id))
        return await guarded(request, rt, work)

    @router.get(base + "/{deployment_id}/readiness", response_model=ReadinessDoc,
                operation_id="getDeploymentReadiness")
    async def readiness(request: Request, deployment_id: str):
        async def work(actor):
            return control.ok(await h.readiness(actor, deployment_id))
        return await guarded(request, rt, work)

    @router.post(base + "/{deployment_id}/smoke", status_code=202,
                 response_model=api.OperationDoc, operation_id="smokeDeployment")
    async def smoke(request: Request, deployment_id: str, key: IdempotencyKey):
        async def work(actor):
            return accepted(await h.smoke(actor, deployment_id, key))
        return await guarded(request, rt, work)

    @router.post(base + "/{deployment_id}/retire", status_code=202,
                 response_model=api.OperationDoc, operation_id="retireDeployment")
    async def retire(request: Request, deployment_id: str, key: IdempotencyKey):
        async def work(actor):
            return accepted(await h.retire(actor, deployment_id, key))
        return await guarded(request, rt, work)

    @router.post("/lab/v1/operations/{operation_id}/cancel", response_model=api.OperationDoc,
                 operation_id="cancelOperation")
    async def cancel(request: Request, operation_id: str):
        async def work(actor):
            return control.ok(await h.cancel(actor, operation_id))
        return await guarded(request, rt, work)

    app.include_router(router)
