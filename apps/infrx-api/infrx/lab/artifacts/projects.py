"""AP-04a/d/e: model projects, their immutable serving revisions and operator adoption.

A project is a provider-scoped slug and card; creating one claims nothing (no catalog row,
no serving revision). A serving revision is built ONLY from a verified artifact of the
project whose compatibility report and requested profile pass, and it is written by the
existing registry door (`LabControl.register` -> A3's immutable `Registry.put`), never a
second registry: 0061 adds only the link serving version -> artifact. Its label is its own
id (no count-based label race). Adoption records an existing production serving version's
independently measured bytes as an `adopted` artifact of its own provider's project; the
serving identity is kept as it is.

Idempotency (R270): every create takes the caller's `Idempotency-Key`; the row id is a pure
function of (action, provider, key) and the row stores the canonical input hash, so a replay
returns the stored row and another body under the same key is `idempotency_conflict`.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime

from pydantic import Field

from ...contracts import api, errors
from ...contracts.api import FieldError, Wire
from ...contracts.v2.records import (CapabilityRecord, DigestSource, ProviderCapability,
                                     ServingRevision)
from ...operations.service import stable_id
from ..access import LabAccess
from ..control import LabControl
from .manifest import Card, FileEntry, Manifest
from .store import Artifact, ArtifactStore, Project, R, Revision, input_hash
from .verify import (ENGINE_OPTIONS_DIGEST, ProfileRequest, artifact_reasons, report,
                     request_reasons, shards)

C = ProviderCapability


class Unsupported(errors.InvalidRequest):
    """A 422 whose `field_errors` name every specific reason (R270's envelope)."""

    def __init__(self, reasons: Sequence[FieldError]) -> None:
        super().__init__("; ".join(r.code for r in reasons))
        self.reasons = tuple(reasons)


# ===================================================================== wire bodies ===
class ProjectRequest(Wire):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    description: str = Field(default="", max_length=4000)


class RevisionRequest(Wire):
    artifact_id: str = Field(max_length=64)
    prompt_harness_ref: str = Field(default="marlin2b.chat.v1", min_length=1, max_length=200)
    preprocessor_profile_version: str = Field(default="marlin2b.video.v1", min_length=1,
                                              max_length=200)
    profile: ProfileRequest = ProfileRequest()


class RevisionDoc(Wire):
    serving_version_id: str
    project_id: str
    artifact_id: str
    public_model_id: str
    revision_label: str
    model_revision: str
    profile: dict
    created_at: datetime


class AdoptRequest(Wire):
    provider_org_id: str = Field(max_length=64)
    serving_version_id: str = Field(max_length=64)
    files: tuple[FileEntry, ...] = Field(min_length=1, max_length=256)
    evidence_ref: str = Field(min_length=1, max_length=500)
    card: Card = Card()


# ========================================================================== paging ===
def page(rows: Sequence[R], cursor: str | None, limit: int) -> api.ListPage:
    """Keyset over (created_at, id), the store's order. The cursor is opaque to the caller
    and scoped by the provider the rows were read under. ponytail: the provider's rows are
    read whole and paged here; a SQL keyset when a provider holds thousands."""
    if not 1 <= limit <= api.MAX_PAGE_SIZE:
        raise errors.InvalidRequest("limit is 1..100")
    after: tuple[str, str] | None = None
    if cursor:
        try:
            after = tuple(json.loads(base64.urlsafe_b64decode(cursor)))  # type: ignore[assignment]
        except (ValueError, binascii.Error, TypeError):
            raise errors.InvalidCursor("not a cursor this list issued") from None
    rest = [r for r in rows if after is None
            or (r.created_at.isoformat(), r.key) > (after[0], after[1])]
    chunk = rest[:limit]
    following = None
    if len(rest) > limit:
        last = chunk[-1]
        following = base64.urlsafe_b64encode(json.dumps(
            [last.created_at.isoformat(), last.key]).encode()).decode()
    return api.ListPage(data=tuple(chunk), next_cursor=following)


class Projects:
    def __init__(self, access: LabAccess, store: ArtifactStore, control: LabControl) -> None:
        self.access, self.store, self.control = access, store, control

    async def _project(self, actor: api.Actor, project_id: str, capability: C) -> Project:
        provider = _provider(actor)
        await self.access.require(actor.user_id or "", provider, capability)
        project = await self.store.get(Project, provider, project_id)
        if project is None:
            raise errors.NotFound("no such model project in this provider workspace")
        return project

    async def create(self, actor: api.Actor, body: ProjectRequest, key: str) -> Project:
        provider = _provider(actor)
        await self.access.require(actor.user_id or "", provider, C.manage_dev_deployment)
        digest = input_hash(body.model_dump(mode="json"))
        stored = await self.store.insert(Project(
            project_id=stable_id("model_project", provider, key), provider_org_id=provider,
            slug=body.slug, name=body.name, description=body.description,
            request_hash=digest, created_by=actor.user_id or "",
            created_at=await self.store.db_now()))
        return _same(stored, digest)

    async def list(self, actor: api.Actor) -> list[Project]:
        provider = _provider(actor)
        await self.access.require(actor.user_id or "", provider, C.read_aggregate_health)
        return await self.store.rows(Project, provider)

    async def create_revision(self, actor: api.Actor, project_id: str, body: RevisionRequest,
                              key: str) -> RevisionDoc:
        project = await self._project(actor, project_id, C.manage_dev_deployment)
        provider = project.provider_org_id
        artifact = await self.store.get(Artifact, provider, body.artifact_id)
        if artifact is None or artifact.project_id != project.project_id:
            raise errors.NotFound("no such verified artifact in this model project")
        reasons = [*artifact.compatibility.reasons, *request_reasons(body.profile)]
        if reasons:
            raise Unsupported(reasons)
        digest = input_hash(body.model_dump(mode="json"))
        link = _same(await self.store.insert(Revision(
            serving_version_id=stable_id("serving_revision", provider, key),
            project_id=project.project_id, provider_org_id=provider,
            artifact_id=artifact.artifact_id, profile=body.model_dump(mode="json"),
            request_hash=digest, created_by=actor.user_id or "",
            created_at=await self.store.db_now())), digest)
        project = await self.store.bind(project)
        await self.control.register(actor.user_id or "", provider,
                                    _serving(project, artifact, link, body))
        return _doc(project, link)

    async def revisions(self, actor: api.Actor, project_id: str) -> list[RevisionDoc]:
        project = await self._project(actor, project_id, C.read_aggregate_health)
        return [_doc(project, link) for link in await self.store.rows(
            Revision, project.provider_org_id, project_id=project.project_id)]

    # --- 04e: the operator's adoption of an existing production serving version -------
    async def adopt(self, actor: api.Actor, body: AdoptRequest) -> Artifact:
        if not actor.operator or actor.user_id is None:
            raise errors.Forbidden("an operator adopts existing artifacts")
        serving = await self.control.catalog.serving_revision(body.serving_version_id)
        if serving is None or serving.provider_org_id != body.provider_org_id:
            raise errors.NotFound("no such serving version in this provider workspace")
        manifest = Manifest(files=body.files)
        by_path = manifest.by_path()
        measured = {"tokenizer.json": serving.tokenizer_digest,
                    "chat_template.jinja": serving.chat_template_digest}
        mismatched = [FieldError(field=f"files.{name}", code="digest_mismatch",
                                 message="the measured bytes are not the serving pin")
                      for name, pin in measured.items()
                      if name not in by_path or by_path[name].sha256 != pin]
        if [by_path[s].sha256 for s in shards(manifest)] != list(serving.weight_shard_digests):
            mismatched.append(FieldError(field="files", code="digest_mismatch",
                                         message="the measured shards are not the serving pins"))
        if mismatched:
            raise Unsupported(mismatched)
        principal, now = f"operator:{actor.user_id}", await self.store.db_now()
        bound = await self.store.rows(Project, serving.provider_org_id,
                                      model_uuid=serving.model_id)
        project = bound[0] if bound else await self.store.insert(Project(
            project_id=stable_id("adopted_project", serving.model_id),
            provider_org_id=serving.provider_org_id,
            slug=serving.public_model_id.rpartition("/")[2], name=serving.public_model_id,
            description="", model_uuid=serving.model_id,
            public_model_id=serving.public_model_id,
            request_hash=input_hash({"adopted": serving.model_id}), created_by=principal,
            created_at=now))
        artifact = await self.store.insert(Artifact(
            artifact_id=stable_id("adopted_artifact", serving.serving_version_id),
            project_id=project.project_id, provider_org_id=serving.provider_org_id,
            source="adopted", source_repo=serving.model_repo, source_commit=serving.model_commit,
            files=manifest.files, manifest_sha256=manifest.digest, card=body.card,
            provenance={"serving_version_id": serving.serving_version_id,
                        "evidence_ref": body.evidence_ref, "measured_by": principal,
                        "digest_source": DigestSource.served_bytes.value},
            # the bytes are the production pins' own (checked above); config not re-read
            compatibility=report(artifact_reasons(manifest, None, None)),
            verified_at=now, created_by=principal, created_at=now))
        if artifact.manifest_sha256 != manifest.digest:
            raise errors.IdempotencyConflict("this serving version was adopted with other bytes")
        await self.store.insert(Revision(
            serving_version_id=serving.serving_version_id, project_id=project.project_id,
            provider_org_id=serving.provider_org_id, artifact_id=artifact.artifact_id,
            profile={"adopted": True}, request_hash=manifest.digest, created_by=principal,
            created_at=now))
        return artifact


def _provider(actor: api.Actor) -> str:
    if actor.provider_org_id is None or actor.user_id is None:
        raise errors.Forbidden("a provider workspace session is required")
    return actor.provider_org_id


def _same(stored: R, digest: str) -> R:
    if getattr(stored, "request_hash") != digest:
        raise errors.IdempotencyConflict("this key was used for another request")
    return stored


def _doc(project: Project, link: Revision) -> RevisionDoc:
    public = project.public_model_id or ""
    return RevisionDoc(serving_version_id=link.serving_version_id, project_id=link.project_id,
                       artifact_id=link.artifact_id, public_model_id=public,
                       revision_label=link.serving_version_id,
                       model_revision=f"{public}@{link.serving_version_id}",
                       profile=link.profile, created_at=link.created_at)


def _serving(project: Project, artifact: Artifact, link: Revision,
             body: RevisionRequest) -> ServingRevision:
    by_path = {f.relative_path: f.sha256 for f in artifact.files}
    profile = body.profile
    return ServingRevision(
        serving_version_id=link.serving_version_id, model_id=project.model_uuid or "",
        model_version_id=stable_id("model_version", artifact.artifact_id),
        provider_org_id=project.provider_org_id, public_model_id=project.public_model_id or "",
        revision_label=link.serving_version_id,
        # an upload has no source commit: its revision pins the manifest digest's prefix
        model_repo=artifact.source_repo or f"upload/{artifact.artifact_id}",
        model_commit=artifact.source_commit or artifact.manifest_sha256[7:47],
        weight_shard_digests=tuple(by_path[s] for s in shards(Manifest(files=artifact.files))),
        tokenizer_digest=by_path["tokenizer.json"],
        chat_template_digest=by_path["chat_template.jinja"],
        digest_source=DigestSource.served_bytes, prompt_harness_ref=body.prompt_harness_ref,
        preprocessor_profile_version=body.preprocessor_profile_version,
        runtime_image_ref=profile.runtime_image_ref,
        runtime_image_digest=profile.runtime_image_ref.partition("@")[2],
        engine_options_digest=ENGINE_OPTIONS_DIGEST, precision=profile.precision,
        capability=CapabilityRecord(
            input_modalities=("text", "video"), output_modalities=("text",),
            stream_output=True, input_schema_ref=profile.input_schema_ref,
            output_schema_ref=profile.output_schema_ref,
            preprocessing_profile_ref=body.preprocessor_profile_version),
        created_at=link.created_at)
