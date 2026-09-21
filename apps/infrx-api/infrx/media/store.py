"""M1: the `ports.MediaStore` adapter - durable staging behind an object store.

What M1 owns: `stage` (the canonical request payload and its source metadata, durably,
before acceptance), `attach` (bind staged refs to an admitted job) and `resolve_owned`,
plus `materialize`, which turns one caller-supplied URL or `data:` URL into a durable
tenant-scoped object through `fetch.py`. `create_upload`/`finalize_upload` belong to M3
and `prepare` to M2; they raise `NotImplementedError` here rather than a passthrough
that would hand a worker refs to objects nobody prepared.

Two rules run through all of it, and they are the same two the fake encodes:

* the caller never names a path. Every key is built from the tenant, the source digest
  and the profile version, so two organizations with identical bytes never share an
  object and a caller cannot reach or overwrite one by asking for it.
* staging is all or nothing (02: "a staging failure creates no job or hold"). Every
  reference is validated and resolved first; nothing is written or indexed until all of
  them check out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import codec, errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import MediaKind, MediaRef, NormalizedRequest
from .fetch import DATA_PREFIX, HTTP_PREFIXES, MediaFetcher, decode_data_url, digest_of

HANDLE_PREFIX = "med_"
HANDLE_DIGEST_CHARS = 40          # 160 bits of the content digest: opaque enough, and
                                  # content-addressed, so restaging is idempotent


class ObjectStore(Protocol):
    """The injectable object-store client. Deliberately two operations wide, both of
    which an S3-compatible store answers directly (HeadObject, PutObject), so the real
    adapter adds credentials and nothing else."""

    async def head(self, key: str) -> str | None:
        """The digest of the stored object, or None if there is none."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store bytes at a server-built key."""


class InMemoryObjectStore:
    """The unit-test double, and the only object store M1 ships."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, bytes, str]] = {}

    async def head(self, key: str) -> str | None:
        stored = self.objects.get(key)
        return stored[0] if stored else None

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (digest_of(data), bytes(data), content_type)


@dataclass(frozen=True)
class StagedPayload:
    """What `stage` made durable: the immutable body, its digest and its size."""

    ref: str
    digest: str
    bytes: int


def media_handle(digest: str) -> str:
    return HANDLE_PREFIX + digest.split(":")[1][:HANDLE_DIGEST_CHARS]


class MediaStaging:
    """`ports.MediaStore`, for the operations M1 owns."""

    def __init__(self, objects: ObjectStore, *, limits: PilotSettings = DEFAULTS,
                 fetcher: MediaFetcher | None = None, job_org=None,
                 profile_version: str = "v1") -> None:
        self.objects = objects
        self.limits = limits
        self.fetcher = fetcher or MediaFetcher(limits)
        # r1 R55: where `attach` reads a job's organization. A real adapter joins the job
        # row; injected here, and an unknown job is `not_found` rather than a caller's
        # word for which tenant its refs belong to.
        self.job_org = job_org or self._no_jobs
        self.profile_version = profile_version
        # (org, handle) -> immutable ref. Keyed by tenant, so one org's handle can never
        # name, replace or shadow another org's object.
        self.refs: dict[tuple[str, str], MediaRef] = {}
        self.by_job: dict[str, tuple[MediaRef, ...]] = {}
        self.payloads: dict[str, StagedPayload] = {}

    @staticmethod
    def _no_jobs(job_id: str) -> str:
        raise errors.NotFound(f"no job {job_id}")

    def _key(self, org_id: str, digest: str, profile_version: str, part: str) -> str:
        # The tenant, the source digest and the profile version namespace the cache
        # (01: "tenant source digest + profile version namespace both media cache keys").
        return f"media/{org_id}/{profile_version}/{digest.split(':')[1][:16]}/{part}"

    async def _write_once(self, key: str, data: bytes, content_type: str) -> None:
        """Immutable content: the same bytes twice is a no-op, different bytes under a
        key that already exists is a conflict, never a replacement."""
        stored = await self.objects.head(key)
        if stored is None:
            await self.objects.put(key, data, content_type)
            return
        if stored != digest_of(data):
            raise errors.Conflict(f"an object already exists at {key} with different content")

    # --- materialization (M1's own operation, not a port one) ----------------
    async def materialize(self, org_id: str, source: str) -> MediaRef:
        """Fetch or decode one caller-supplied source **once** and store it durably.

        The only place a caller's URL is touched. The returned handle is derived from
        the content digest, so the same source materialized twice is one object and one
        ref rather than two rows pointing at the same bytes.
        """
        if source.startswith(DATA_PREFIX):
            fetched, kind = decode_data_url(source, self.limits), MediaKind.inline
        elif source.startswith(HTTP_PREFIXES):
            fetched, kind = await self.fetcher.fetch(source), MediaKind.url
        else:
            raise errors.InvalidRequest("a media source must be an http(s) or data: URL")
        if len(fetched.data) > self.limits.max_media_bytes:
            raise errors.RequestTooLarge(f"{len(fetched.data)} bytes exceeds MAX_MEDIA_BYTES")
        ref = MediaRef(org_id=org_id, handle=media_handle(fetched.digest), kind=kind,
                       digest=fetched.digest, bytes=len(fetched.data), mime=fetched.mime,
                       storage_ref=self._key(org_id, fetched.digest, self.profile_version,
                                             "source"),
                       profile_version=self.profile_version)
        await self._write_once(ref.storage_ref, fetched.data, fetched.mime)
        self.refs[(org_id, ref.handle)] = ref
        return ref

    def staged_payload(self, request_id: str) -> StagedPayload:
        payload = self.payloads.get(request_id)
        if payload is None:
            raise errors.NotFound(f"no staged payload for request {request_id}")
        return payload

    # --- port ----------------------------------------------------------------
    async def stage(self, org_id: str, request: NormalizedRequest) -> tuple[MediaRef, ...]:
        """Make the canonical request payload and every source reference durable.

        The payload is the whole `NormalizedRequest`, so the source metadata - each
        ref's digest, size, type and duration - is durable with it, and an accepted
        request always has a recoverable body with a digest.
        """
        if request.org_id != org_id:
            raise errors.Forbidden("a request may only be staged for its own org")
        resolved: list[MediaRef] = []
        pending: dict[tuple[str, str], MediaRef] = {}
        for ref in request.media:
            if ref.org_id != org_id:
                raise errors.NotFound("media reference does not belong to this org")
            if ref.bytes > self.limits.max_media_bytes:
                raise errors.RequestTooLarge(
                    f"{ref.bytes} bytes exceeds MAX_MEDIA_BYTES {self.limits.max_media_bytes}")
            if ref.kind is MediaKind.upload:
                # Resolution can fail (unknown handle, another tenant's, not finalized),
                # so it belongs in this pass, before anything is written.
                owned = await self.resolve_owned(org_id, ref.handle)
                if owned.digest != ref.digest:
                    raise errors.UnsupportedMedia("upload digest does not match the reference")
                resolved.append(owned)
                continue
            existing = self.refs.get((org_id, ref.handle))
            if existing is not None:
                # Staged content is immutable: the same handle keeps the object it has,
                # and different content under it is a conflict rather than a replacement.
                if existing.digest != ref.digest:
                    raise errors.Conflict(
                        f"media handle {ref.handle} already holds different content")
                resolved.append(existing)
                continue
            staged = ref.model_copy(update={
                "storage_ref": self._key(org_id, ref.digest, ref.profile_version, "source")})
            clash = pending.get((org_id, staged.handle))
            if clash is not None and clash.digest != staged.digest:
                # The same handle twice in one request with different content: last-wins
                # would stage one object and hand the job the other one's digest.
                raise errors.InvalidRequest(
                    f"media handle {ref.handle} appears twice with different content")
            pending[(org_id, staged.handle)] = staged
            resolved.append(staged)
        payload = codec.canonical_bytes(request)
        # A server-built key from server-known identity; `request.payload_ref` is not read,
        # because a caller-named path is exactly what a media store must never accept.
        key = f"payloads/{org_id}/{request.request_id}.json"
        await self._write_once(key, payload, "application/json")
        # One visible step: nothing above wrote to `self.refs`.
        self.refs.update(pending)
        self.payloads[request.request_id] = StagedPayload(ref=key, digest=digest_of(payload),
                                                          bytes=len(payload))
        return tuple(resolved)

    async def attach(self, job_id: str, refs: tuple[MediaRef, ...]) -> None:
        """r1 R46/R52/R55: bind staged refs to an admitted job, with the organization read
        from the job row and every ref checked against it. All or nothing: a refused
        attach leaves `prepare` with nothing rather than a half-written set."""
        org_id = self.job_org(job_id)
        for ref in refs:
            if ref.org_id != org_id:
                raise errors.NotFound("media attached to a job must belong to its org")
        self.by_job[job_id] = tuple(refs)

    async def resolve_owned(self, org_id: str, ref: str) -> MediaRef:
        # Keyed by tenant: another org's handle simply is not in this org's namespace.
        media = self.refs.get((org_id, ref))
        if media is None:
            raise errors.NotFound(f"no media {ref} owned by org {org_id}")
        return media

    # --- other tracks' operations -------------------------------------------
    async def prepare(self, job_id: str, profile: str) -> tuple[MediaRef, ...]:
        raise NotImplementedError("M2 owns versioned preprocessing")

    async def create_upload(self, org_id: str, constraints: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("M3 owns owned uploads")

    async def finalize_upload(self, org_id: str, upload_handle: str) -> MediaRef:
        raise NotImplementedError("M3 owns owned uploads")
