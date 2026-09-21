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

import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import codec, errors
from ..contracts.ids import UUID_RE
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import MediaKind, MediaRef, NormalizedRequest
from .fetch import DATA_PREFIX, HTTP_PREFIXES, MediaFetcher, decode_data_url, digest_of

HANDLE_PREFIX = "med_"
HANDLE_DIGEST_CHARS = 40          # 160 bits of the content digest: opaque enough, and
                                  # content-addressed, so restaging is idempotent
# A profile version ends up *in an object key*, so it is an identifier and nothing else:
# `profile_version="../../payloads/<another org>"` turned a tenant-scoped key into a path
# into another tenant's prefix (review, cross-track hazard).
PROFILE_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# The third part a key is built from. A `MediaRef` validates it, but `model_copy` does
# not, so a digest reaching `_key` is checked where the key is built and not where the
# record happens to have come from.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def valid_org(org_id: object) -> str:
    """The tenant every key is namespaced by, checked before anything is fetched or
    written: a malformed one then costs no outbound request and raises a typed error
    instead of surfacing as a record-validation failure two steps later."""
    if not isinstance(org_id, str) or not UUID_RE.fullmatch(org_id):
        raise errors.InvalidRequest("org_id must be a lowercase UUID")
    return org_id


def valid_digest(digest: object) -> str:
    """The 16 hex characters a key carries, or a typed refusal."""
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise errors.InvalidRequest("a digest must be sha256:<64 hexadecimal digits>")
    return digest[len("sha256:"):][:16]


def valid_profile(version: object) -> str:
    if not isinstance(version, str) or not PROFILE_VERSION_RE.fullmatch(version):
        raise errors.InvalidRequest("a profile version must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    return version


class ObjectStore(Protocol):
    """The injectable object-store client. Deliberately two operations wide, both of
    which an S3-compatible store answers directly (HeadObject, and PutObject with
    `If-None-Match: *`), so the real adapter adds credentials and nothing else."""

    async def head(self, key: str) -> str | None:
        """The digest of the stored object, or None if there is none."""

    async def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        """Store bytes at a server-built key only if nothing is there; True if written.

        Write-once is this store's most important property, so it is one atomic operation
        rather than a read and a write a concurrent staging can interleave with.
        """


class InMemoryObjectStore:
    """The unit-test double, and the only object store M1 ships."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, bytes, str]] = {}

    async def head(self, key: str) -> str | None:
        stored = self.objects.get(key)
        return stored[0] if stored else None

    async def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = (digest_of(data), bytes(data), content_type)
        return True

    def seed(self, key: str, data: bytes, content_type: str = "video/mp4") -> None:
        """Not part of the port: how a test puts something at a key behind the store's
        back, to prove the store will not replace it."""
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
        # Both parts a caller can influence are validated *here*, so every key M builds -
        # source, prepared, or whatever M2 adds - goes through the same guard.
        return (f"media/{valid_org(org_id)}/{valid_profile(profile_version)}"
                f"/{valid_digest(digest)}/{part}")

    async def _write_once(self, key: str, data: bytes, content_type: str) -> None:
        """Immutable content: the same bytes twice is a no-op, different bytes under a
        key that already exists is a conflict, never a replacement."""
        if await self.objects.put_if_absent(key, data, content_type):
            return
        if await self.objects.head(key) != digest_of(data):
            raise errors.Conflict(f"an object already exists at {key} with different content")

    # --- materialization (M1's own operation, not a port one) ----------------
    async def materialize(self, org_id: str, source: str) -> MediaRef:
        """Fetch or decode one caller-supplied source **once** and store it durably.

        The only place a caller's URL is touched. The returned handle is derived from
        the content digest, so the same source materialized twice is one object and one
        ref rather than two rows pointing at the same bytes.
        """
        org_id = valid_org(org_id)          # before any outbound request is made
        if source.startswith(DATA_PREFIX):
            fetched, kind = decode_data_url(source, self.limits, self.fetcher.allowed_mime), \
                MediaKind.inline
        elif source.startswith(HTTP_PREFIXES):
            fetched, kind = await self.fetcher.fetch(source), MediaKind.url
        else:
            raise errors.InvalidRequest("a media source must be an http(s) or data: URL")
        if len(fetched.data) > self.limits.max_media_bytes:
            raise errors.RequestTooLarge(f"{len(fetched.data)} bytes exceeds MAX_MEDIA_BYTES")
        # Measured here rather than taken from the fetcher: the digest is the object's
        # identity, its key and its handle, so it is computed from the bytes being stored.
        digest = digest_of(fetched.data)
        ref = MediaRef(org_id=org_id, handle=media_handle(digest), kind=kind,
                       digest=digest, bytes=len(fetched.data), mime=fetched.mime,
                       storage_ref=self._key(org_id, digest, self.profile_version, "source"),
                       profile_version=self.profile_version)
        clash = self.refs.get((org_id, ref.handle))
        if clash is not None and clash.digest != digest:
            # The handle is a prefix of the digest, so this cannot happen by chance; if it
            # ever does (a shorter handle, a different scheme) it must not silently
            # replace the tenant's ref with one pointing at other content.
            raise errors.Conflict(f"handle {ref.handle} already names different content")
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
                # The **indexed** ref, never the caller's copy of it: the request's
                # `bytes`, `mime`, `duration_s` and `storage_ref` are claims, and the
                # object's own facts are what a job must carry (review B4/S03).
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
        key = f"payloads/{valid_org(org_id)}/{request.request_id}.json"
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
        owned: list[MediaRef] = []
        for ref in refs:
            if ref.org_id != org_id:
                raise errors.NotFound("media attached to a job must belong to its org")
            # And it must be a ref this store staged for that org: a job may only execute
            # on media the store itself put somewhere, described as the store described it.
            # Attaching an own-org ref that was never staged used to bind a job to an
            # object that does not exist, with fields the caller chose (review).
            indexed = self.refs.get((org_id, ref.handle))
            if indexed is None or indexed.digest != ref.digest:
                raise errors.NotFound(f"media {ref.handle} was not staged for org {org_id}")
            owned.append(indexed)
        self.by_job[job_id] = tuple(owned)

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
