"""AP-04 composition (WR-AP04-2): the artifact surface on 0060's durable operations.

- `DurableOps`: AP-04's operation port (`store.ControlOps`, what `uploads`/`imports`/the
  routes call) over api-schema's 0060 store (`state.control_ops`: `PgControlOps`, and
  `FakeControlOps` in tests). It replaces `store.MemoryControlOps` wherever AP-04 is
  composed: an operation outlives the process that started or worked it.
- `surface(connect, objects)`: `LabArtifacts` on one login (the Lab unit's
  `INFRX_LAB_DATABASE_URL`, or the worker role's `LAB_DATABASE_URL`) over 0061
  (`PgArtifactStore`), 0060 and the Lab objects (`S3Objects` over the same
  `S3ObjectStore` the other Lab families use, `workers.lab_objects`).
- `mount(app, rt)`: the two AP-04 route families on the Lab unit, behind `WorkspaceActors`.
- `role(...)`: `python -m infrx.lab.workers artifacts` - `ArtifactWorker` every `PASS_S`.
- `secrets(refs)`: the worker's secret resolver, limited to the references its role lists.
- `files_from`/`import_request`: the coordinator's real import body from the box manifest
  (`infra/runbooks/artifacts.py manifest`).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request

from ...contracts import api, errors
from ...state import control_ops
from . import ArtifactWorker, LabArtifacts
from .imports import HubSource, SourceRefused, env_secret
from .manifest import refuse_path
from .store import Operation

PASS_S = 5.0                          # ponytail: fixed cadence, as the datasets import pass
SECRET_REFS = "LAB_ARTIFACT_SECRET_REFS"

#: who reads/cancels for the system: the routes scope a read to the caller's workspace
#: themselves (`routes/lab_artifacts.py`), the worker reads nothing
SYSTEM = api.Actor(audience="operator", operator=True)


def _op(op: control_ops.Operation) -> Operation:
    return Operation(op.doc(), op.actor, op.resource_kind or "", "", "", "", op.fence,
                     op.lease_owner)


class DurableOps:
    """AP-04's `ControlOps` over 0060's. The mapping: `start`'s scope is 0060's own (the
    actor's tenant + kind); a lease another holder has (or a finished operation) is None,
    not a refusal; a lost fence is `StaleLease`. Every `advance` first renews the caller's
    own lease (the heartbeat: one phase is one file, a whole verification may outlast the
    ttl)."""

    def __init__(self, ops: control_ops.ControlOps) -> None:
        self.ops = ops
        self.held: dict[tuple[str, int], tuple[str, int]] = {}   # (op, fence) -> (owner, ttl)

    async def start(self, *, kind: str, resource_kind: str, resource_id: str,
                    actor: api.Actor, scope: str, key: str,
                    input_hash: str) -> tuple[Operation, bool]:
        started = await self.ops.start(kind, actor, key, input_hash,
                                       resource_kind=resource_kind, resource_id=resource_id)
        return _op(started.operation), started.replayed

    async def get(self, operation_id: str) -> Operation:
        return _op(await self.ops.get(operation_id, SYSTEM))

    async def lease(self, operation_id: str, owner: str, ttl_s: int) -> int | None:
        try:
            op = await self.ops.lease(operation_id, owner, ttl_s)
        except errors.Conflict:
            return None
        self.held[(operation_id, op.fence)] = (owner, ttl_s)
        return op.fence

    async def advance(self, operation_id: str, fence: int, phase: str) -> Operation:
        try:
            if (operation_id, fence) in self.held:
                await self.ops.lease(operation_id, *self.held[(operation_id, fence)])
            return _op(await self.ops.advance(operation_id, fence, phase))
        except errors.Conflict:
            raise errors.StaleLease("a newer lease holds this operation") from None

    async def finish(self, operation_id: str, fence: int, state: str,
                     error: api.ErrorBody | None = None) -> Operation:
        self.held.pop((operation_id, fence), None)
        try:
            return _op(await self.ops.finish(operation_id, fence, state, error))
        except errors.Conflict:
            raise errors.StaleLease("a newer lease holds this operation") from None

    async def cancel(self, operation_id: str) -> Operation:
        return _op(await self.ops.cancel(operation_id, SYSTEM))


class WorkspaceActors:
    """`rt.actors` for the AP-04 routes on the Lab unit. AP-04 reads the workspace from the
    actor; a web session (AP-01's `SessionActors`) names none, so it is the request's
    `?provider_org_id=` - what every Lab request carries (`apps/lab` `labClient`). Only a
    claim: every AP-04 door checks the session user's membership and role in that workspace
    (`LabAccess.require`). A credential that carries its own provider keeps it. Without
    composed session actors (AP-01 on the unit) every call is a 503."""

    def __init__(self, actors: Any) -> None:
        self.actors = actors

    async def actor(self, request: Request) -> api.Actor:
        if self.actors is None:
            raise errors.DependencyUnavailable("the Lab session actors are not composed (AP-01)")
        actor = await self.actors.actor(request)
        if actor.audience != "session" or actor.provider_org_id is not None:
            return actor
        return actor.model_copy(update={
            "provider_org_id": request.query_params.get("provider_org_id") or None})


def mount(app: FastAPI, rt: Any) -> None:
    """The Lab unit's AP-04 families (WR-AP04-2); nothing while `rt.lab_artifacts` is None."""
    from ...gateway.routes import lab_artifacts, lab_model_projects
    scoped = SimpleNamespace(actors=WorkspaceActors(getattr(rt, "actors", None)),
                             lab_artifacts=getattr(rt, "lab_artifacts", None))
    lab_model_projects.register(app, scoped)
    lab_artifacts.register(app, scoped)


def secrets(refs: str, ssm: Any = None):
    """The import worker's resolver for exactly the references `refs` lists (comma-separated,
    the role's `LAB_ARTIFACT_SECRET_REFS`): a provider names a reference, and an unlisted one
    (another platform secret) is `secret_unavailable` before anything is read. `env:NAME`
    from the environment; `ssm:/path` by SSM GetParameter, decrypted, on the instance role."""
    allowed = {ref.strip() for ref in refs.split(",") if ref.strip()}

    def resolve(ref: str) -> str:
        if ref not in allowed:
            raise SourceRefused("secret_unavailable")
        if ref.startswith("env:"):
            return env_secret(ref)
        try:
            import boto3
            client = ssm or boto3.client("ssm")
            return client.get_parameter(Name=ref.removeprefix("ssm:"),
                                        WithDecryption=True)["Parameter"]["Value"]
        except Exception:                 # noqa: BLE001 - never the reason: it may name it
            raise SourceRefused("secret_unavailable") from None
    return resolve


def surface(connect: Any, objects: Any, *, source: HubSource | None = None,
            secret_refs: str = "", ssm: Any = None) -> LabArtifacts:
    """`LabArtifacts` on one login; `objects` is the Lab objects' `S3ObjectStore`."""
    from ...state.lab_access import PgAccessStore
    from ..access import LabAccess
    from ..compose import lab_control
    from .store import PgArtifactStore, S3Objects
    access = LabAccess(PgAccessStore(connect))
    return LabArtifacts.compose(access, lab_control(connect, access), PgArtifactStore(connect),
                                DurableOps(control_ops.PgControlOps(connect)),
                                S3Objects(objects), source,
                                secret=secrets(secret_refs, ssm))


def role(mode: str, env: Any, connect: Any, objects: Any, worker_id: str,
         source: HubSource | None = None, **_: Any) -> tuple[dict, ArtifactWorker]:
    """`python -m infrx.lab.workers artifacts` (`BUILD["artifacts"]`, needs LAB_S3_BUCKET):
    verification and import operations, and the expiry sweep, every `PASS_S`."""
    from ...worker.__main__ import every
    worker = ArtifactWorker(surface(connect, objects, source=source,
                                    secret_refs=env.get(SECRET_REFS, "")), worker_id)
    return {"artifacts": lambda: every(PASS_S, worker.run_once, "artifact pass")}, worker


def files_from(doc: dict) -> list[dict]:
    """A manifest's `files` (`artifacts.py`: path/bytes/sha256) as AP-04 file entries, leaving
    out what is not an artifact path (a hidden or code-bearing file)."""
    return [{"relative_path": e["path"], "bytes": e["bytes"], "sha256": e["sha256"],
             "media_type": "application/json" if e["path"].endswith(".json")
             else "application/octet-stream"}
            for e in doc["files"] if refuse_path(e["path"]) is None]


def import_request(doc: dict, project_id: str, secret_ref: str) -> dict:
    """`POST /lab/v1/artifacts/imports`'s body for the manifest's repository at its commit."""
    return {"project_id": project_id, "files": files_from(doc), "secret_ref": secret_ref,
            "source": {"host": "huggingface.co", "repo": doc["model"]["repo"],
                       "commit": doc["model"]["commit"]}}


__all__ = ["DurableOps", "WorkspaceActors", "files_from", "import_request", "mount", "role",
           "secrets", "surface"]
