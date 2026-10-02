"""AP-04c: pinned repository import from an allowlisted source.

The request names a source host from `SOURCES` (never a URL: no arbitrary server-side
fetching), a repository, a 40-hex commit (a branch, tag or short sha is a mutable ref and is
refused) and optionally a secret REFERENCE (`env:NAME` | `ssm:/path`) the worker resolves at
fetch time - the token itself is never accepted, stored, echoed or logged. The worker fetches
each declared file at that commit into the object store while hashing it, then the shared
verification (`uploads.settle`) decides; a source that refuses the credential, lacks a file
or redirects off its own hosts fails the operation with that reason.
"""
from __future__ import annotations

import os
import re
import tempfile
from urllib.parse import quote

import httpx
from pydantic import Field, field_validator

from ...contracts import api
from ...contracts.api import FieldError, Wire
from ...operations.service import stable_id
from ..access import LabAccess
from .manifest import Card, Manifest
from .store import ArtifactStore, ControlOps, Import, Objects, Operation, input_hash
from .uploads import Cancelled, _fail, prefix, project_of, settle

#: allowlisted source host -> its base URL (tests point the same host at a local stub)
SOURCES = {"huggingface.co": "https://huggingface.co"}
#: hosts a source may redirect a file to (LFS/Xet storage), besides its own
REDIRECT_SUFFIXES = (".huggingface.co", ".hf.co")
COMMIT = "^[0-9a-f]{40}$"
REPO = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
SECRET_REF = r"^(env:[A-Z][A-Z0-9_]{0,63}|ssm:/[A-Za-z0-9_./-]{1,200})$"
FETCH_TIMEOUT_S = 60.0


class ImportSource(Wire):
    host: str = Field(max_length=100)
    repo: str = Field(pattern=REPO)
    commit: str = Field(max_length=100)

    @field_validator("host")
    @classmethod
    def _allowlisted(cls, host: str) -> str:
        if host not in SOURCES:
            raise ValueError("prohibited_source: not an allowlisted source host")
        return host

    @field_validator("commit")
    @classmethod
    def _pinned(cls, commit: str) -> str:
        if not re.fullmatch(COMMIT, commit):
            raise ValueError("mutable_ref: an import is pinned to a 40-hex commit")
        return commit


class ImportRequest(Manifest):
    project_id: str = Field(max_length=64)
    source: ImportSource
    secret_ref: str | None = Field(default=None, max_length=210)
    card: Card = Card()

    @field_validator("secret_ref")
    @classmethod
    def _reference(cls, ref: str | None) -> str | None:
        if ref is not None and not re.fullmatch(SECRET_REF, ref):
            raise ValueError("not_a_secret_reference: env:NAME or ssm:/path, never a token")
        return ref


class SourceRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def env_secret(ref: str) -> str:
    """`env:NAME` from the worker's environment. ponytail: `ssm:` resolves on the box's
    worker (the SSM-read wrapper there), not in this process."""
    kind, _, name = ref.partition(":")
    value = os.environ.get(name) if kind == "env" else None
    if not value:
        raise SourceRefused("secret_unavailable")
    return value


class HubSource:
    """Files of a Hugging Face-style repository at a commit: `<base>/<repo>/resolve/<commit>/
    <path>`, streamed to disk (the shared verification rehashes the stored copy)."""

    def __init__(self, bases: dict[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.bases, self.transport = bases or SOURCES, transport

    def _allowed(self, host: str) -> bool:
        return (host in {httpx.URL(b).host for b in self.bases.values()}
                or host.endswith(REDIRECT_SUFFIXES))

    async def _guard(self, request: httpx.Request) -> None:
        if not self._allowed(request.url.host):
            raise SourceRefused("redirect_refused")

    async def fetch(self, host: str, repo: str, commit: str, path: str, token: str | None,
                    dest) -> None:
        url = f"{self.bases[host]}/{repo}/resolve/{commit}/{quote(path)}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT_S,
                                     transport=self.transport,
                                     event_hooks={"request": [self._guard]}) as client:
            try:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise SourceRefused("source_unauthorized")
                    if response.status_code == 404:
                        raise SourceRefused("missing_at_source")
                    if response.status_code != 200:
                        raise SourceRefused("source_unavailable")
                    async for chunk in response.aiter_bytes(1 << 20):
                        dest.write(chunk)
            except httpx.HTTPError:
                raise SourceRefused("source_unavailable") from None


class Imports:
    def __init__(self, access: LabAccess, store: ArtifactStore, ops: ControlOps,
                 objects: Objects, source: HubSource | None = None, secret=env_secret) -> None:
        self.access, self.store, self.ops, self.objects = access, store, ops, objects
        self.source, self.secret = source or HubSource(), secret

    async def start(self, actor: api.Actor, body: ImportRequest, key: str) -> Operation:
        project = await project_of(self.access, self.store, actor, body.project_id)
        provider, digest = project.provider_org_id, input_hash(body.model_dump(mode="json"))
        import_id = stable_id("artifact_import", provider, key)
        op, _ = await self.ops.start(
            kind="artifact.import", resource_kind="artifact",
            resource_id=stable_id("artifact", import_id), actor=actor,
            scope=f"artifact.import:{provider}", key=key, input_hash=digest)
        now = await self.store.db_now()
        await self.store.insert(Import(
            import_id=import_id, project_id=project.project_id, provider_org_id=provider,
            source_host=body.source.host, source_repo=body.source.repo,
            source_commit=body.source.commit, secret_ref=body.secret_ref, files=body.files,
            manifest_sha256=body.digest, card=body.card, state="queued",
            operation_id=op.doc.operation_id, request_hash=digest,
            created_by=actor.user_id or "", created_at=now, updated_at=now))
        return op

    async def run(self, row: Import, fence: int) -> None:
        """Fetch every declared file at the pinned commit into the store, then settle."""
        op_id = row.operation_id
        try:
            token = self.secret(row.secret_ref) if row.secret_ref else None
            for entry in Manifest(files=row.files).files:
                if (await self.ops.advance(op_id, fence, f"fetching:{entry.relative_path}")
                        ).doc.state == "cancel_requested":
                    raise Cancelled
                with tempfile.NamedTemporaryFile() as spool:
                    await self.source.fetch(row.source_host, row.source_repo,
                                            row.source_commit, entry.relative_path, token, spool)
                    spool.flush()
                    await self.objects.put_file(prefix(row) + entry.relative_path, spool.name)
        except SourceRefused as refused:
            await _fail(self.store, self.ops, self.objects, row, fence, [FieldError(
                field="source", code=refused.code, message="the source refused the import")],
                "failed")
            return
        except Cancelled:
            await _fail(self.store, self.ops, self.objects, row, fence, [], "cancelled")
            return
        await settle(self.store, self.ops, self.objects, row, fence, source="import",
                     source_repo=row.source_repo, source_commit=row.source_commit,
                     provenance={"import_id": row.import_id, "source_host": row.source_host,
                                 "object_prefix": prefix(row)})


__all__ = ["HubSource", "ImportRequest", "ImportSource", "Imports", "SourceRefused",
           "env_secret"]
