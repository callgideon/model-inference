"""AP-04b: resumable bounded upload sessions and their verification.

A session declares its manifest up front and lives `UPLOAD_TTL_S`. Bytes go straight to the
object store on API-issued presigned PUTs, one per declared path, in any order and as often
as needed while the session is open (resume = ask for the path's URL again); the browser
holds no storage credential and the API never proxies weights. Completion only starts an
operation (202): the worker rehashes the STORED bytes against the declaration, refuses
missing, extra and mismatched paths, and only then writes the immutable artifact. Unverified
bytes are deleted; an expired session is swept with its bytes. A duplicate completion
returns the session's one operation.
"""
from __future__ import annotations

from datetime import timedelta

from pydantic import Field

from ...contracts import api, errors
from ...contracts.api import FieldError, Wire
from ...contracts.v2.records import ProviderCapability
from ...operations.service import stable_id
from ..access import LabAccess
from .manifest import SHA256, Card, Manifest
from .store import (Artifact, ArtifactStore, ControlOps, Import, Objects, Operation, Project,
                    Upload, input_hash)
from .verify import byte_reasons, compatibility

C = ProviderCapability
UPLOAD_TTL_S = 24 * 3600
PART_URL_TTL_S = 15 * 60


class UploadRequest(Manifest):
    project_id: str = Field(max_length=64)
    card: Card = Card()


class PartRequest(Wire):
    relative_path: str = Field(max_length=512)


class PartGrant(Wire):
    relative_path: str
    method: str = "PUT"
    url: str
    expires_s: int


class CompleteRequest(Wire):
    manifest_sha256: str = Field(pattern=SHA256)


class Cancelled(Exception):
    """The operation was cancelled between two files: stop, clean up, record it."""


def prefix(row: Upload | Import) -> str:
    """The backend-allocated key prefix of one intake's bytes."""
    return f"artifacts/{row.provider_org_id}/{row.key}/"


async def project_of(access: LabAccess, store: ArtifactStore, actor: api.Actor,
                     project_id: str) -> Project:
    if actor.provider_org_id is None or actor.user_id is None:
        raise errors.Forbidden("a provider workspace session is required")
    await access.require(actor.user_id, actor.provider_org_id, C.manage_dev_deployment)
    project = await store.get(Project, actor.provider_org_id, project_id)
    if project is None:
        raise errors.NotFound("no such model project in this provider workspace")
    return project


async def settle(store: ArtifactStore, ops: ControlOps, objects: Objects,
                 row: Upload | Import, fence: int, **source) -> None:
    """The verification both intakes share, under the operation's fence: rehash, report,
    then the artifact - or the failure with every reason and the bytes removed."""
    op_id, where = row.operation_id or "", prefix(row)

    async def advance(phase: str) -> None:
        if (await ops.advance(op_id, fence, phase)).doc.state == "cancel_requested":
            raise Cancelled

    manifest = Manifest(files=row.files)
    try:
        reasons, found = await byte_reasons(objects, where, manifest, advance)
        await advance("settling")
    except Cancelled:
        await _fail(store, ops, objects, row, fence, [], "cancelled")
        return
    if reasons:
        await _fail(store, ops, objects, row, fence, reasons, "failed")
        return
    now = await store.db_now()
    artifact = await store.insert(Artifact(
        artifact_id=stable_id("artifact", row.key), project_id=row.project_id,
        provider_org_id=row.provider_org_id, files=row.files, card=row.card,
        manifest_sha256=row.manifest_sha256, compatibility=await compatibility(
            objects, where, manifest), verified_at=now, created_by=row.created_by,
        created_at=now, **source))
    changes: dict[str, object] = {"state": "verified", "artifact_id": artifact.artifact_id}
    if isinstance(row, Upload):
        changes["received"] = tuple(found)
    await store.update(row.model_copy(update=changes), expected_state=row.state)
    await ops.finish(op_id, fence, "succeeded")


async def _fail(store: ArtifactStore, ops: ControlOps, objects: Objects, row: Upload | Import,
                fence: int, reasons: list[FieldError], state: str) -> None:
    op_id = row.operation_id or ""
    for key in await objects.keys(prefix(row)):      # unverified bytes are never kept
        await objects.delete(key)
    await store.update(row.model_copy(update={"state": "failed"}), expected_state=row.state)
    error = None if state == "cancelled" else api.ErrorBody(
        code="invalid_request", message="the artifact failed verification", request_id=op_id,
        retryable=False, field_errors=tuple(reasons), operation_id=op_id)
    await ops.finish(op_id, fence, state, error)


class Uploads:
    def __init__(self, access: LabAccess, store: ArtifactStore, ops: ControlOps,
                 objects: Objects) -> None:
        self.access, self.store, self.ops, self.objects = access, store, ops, objects

    async def create(self, actor: api.Actor, body: UploadRequest, key: str) -> Upload:
        project = await project_of(self.access, self.store, actor, body.project_id)
        digest, now = input_hash(body.model_dump(mode="json")), await self.store.db_now()
        stored = await self.store.insert(Upload(
            upload_id=stable_id("artifact_upload", project.provider_org_id, key),
            project_id=project.project_id, provider_org_id=project.provider_org_id,
            files=body.files, manifest_sha256=body.digest, card=body.card, state="open",
            expires_at=now + timedelta(seconds=UPLOAD_TTL_S), request_hash=digest,
            created_by=actor.user_id or "", created_at=now, updated_at=now))
        if stored.request_hash != digest:
            raise errors.IdempotencyConflict("this key was used for another request")
        return stored

    async def _session(self, actor: api.Actor, upload_id: str) -> Upload:
        if actor.provider_org_id is None or actor.user_id is None:
            raise errors.Forbidden("a provider workspace session is required")
        await self.access.require(actor.user_id, actor.provider_org_id, C.manage_dev_deployment)
        upload = await self.store.get(Upload, actor.provider_org_id, upload_id)
        if upload is None:
            raise errors.NotFound("no such upload session in this provider workspace")
        return upload

    async def _expired(self, upload: Upload) -> bool:
        return upload.state == "expired" or (
            upload.state == "open" and await self.store.db_now() >= upload.expires_at)

    async def part(self, actor: api.Actor, upload_id: str, body: PartRequest) -> PartGrant:
        upload = await self._session(actor, upload_id)
        if await self._expired(upload):
            raise errors.UploadExpired("the upload session has expired")
        if upload.state != "open":
            raise errors.StateConflict("the upload session is no longer accepting bytes")
        if body.relative_path not in Manifest(files=upload.files).by_path():
            raise errors.InvalidRequest("not a path this session declared")
        remaining = int((upload.expires_at - await self.store.db_now()).total_seconds())
        expires = max(1, min(PART_URL_TTL_S, remaining))
        url = await self.objects.presign_put(prefix(upload) + body.relative_path,
                                             expires_s=expires)
        return PartGrant(relative_path=body.relative_path, url=url, expires_s=expires)

    async def complete(self, actor: api.Actor, upload_id: str, body: CompleteRequest,
                       key: str) -> Operation:
        upload = await self._session(actor, upload_id)
        if upload.operation_id is not None:          # a duplicate completion: its one operation
            found = await self.ops.get(upload.operation_id)
            if found is not None:
                return found
        if await self._expired(upload):
            raise errors.UploadExpired("the upload session has expired")
        if body.manifest_sha256 != upload.manifest_sha256:
            raise errors.InvalidRequest("the declared manifest is not this session's")
        op, replayed = await self.ops.start(
            kind="artifact.upload.verify", resource_kind="artifact",
            resource_id=stable_id("artifact", upload.upload_id), actor=actor,
            scope=f"artifact.upload.complete:{upload.provider_org_id}", key=key,
            input_hash=input_hash({"upload_id": upload_id, **body.model_dump()}))
        try:
            await self.store.update(upload.model_copy(update={
                "state": "verifying", "operation_id": op.doc.operation_id}),
                expected_state="open")
        except errors.StateConflict:                 # a concurrent completion won the session
            if not replayed:                         # a replay is the winner's own operation
                await self.ops.cancel(op.doc.operation_id)
            return await self.complete(actor, upload_id, body, key)
        return op

    async def verify(self, upload: Upload, fence: int) -> None:
        await settle(self.store, self.ops, self.objects, upload, fence, source="upload",
                     provenance={"upload_id": upload.upload_id, "object_prefix": prefix(upload)})

    async def expire(self) -> int:
        """Sweep open sessions past their expiry: state `expired`, bytes deleted."""
        swept = 0
        for upload in await self.store.pending(Upload, ("open",)):
            if await self._expired(upload):
                try:
                    await self.store.update(upload.model_copy(update={"state": "expired"}),
                                            expected_state="open")
                except errors.StateConflict:
                    continue                          # completed meanwhile
                for key in await self.objects.keys(prefix(upload)):
                    await self.objects.delete(key)
                swept += 1
        return swept
