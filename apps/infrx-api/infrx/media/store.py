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

import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import codec, errors
from ..contracts.ids import UUID_RE
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import MediaKind, MediaRef, NormalizedRequest
from ..contracts.v2.lifecycle import ContentIdentity, ContentKind, ContentLocation, ContentOrigin
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
#: M6: the most entries any of this process's maps keeps. Each is a copy of a durable record
#: (the content rows, D2's attachments, the staged payload object) or a hand-off inside one
#: request, so a restart - or an eviction - loses nothing a later request needs to be right.
MAX_PROCESS_ENTRIES = 4096


class Recent(OrderedDict):
    """A map keeping only its `limit` most recently written entries (M6: no per-process map
    grows with the tickets, jobs or requests a long-lived process has seen).
    ponytail: evicts by write order, not reads; LRU-on-read if a hot entry ever matters.
    ponytail (M6 R5): a hand-off inside one request (`refs`, `payloads`, `by_job`) lives here
    too, so more than `limit` writes by other requests between its write and its read
    evict it and that request loses its hand-off (the durable record stays). Unmeasured;
    hold the hand-off in the request itself if in-flight requests ever approach `limit`."""

    def __init__(self, limit: int | None = None) -> None:
        super().__init__()
        self.limit = limit or MAX_PROCESS_ENTRIES

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)


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

    async def get(self, key: str) -> bytes | None:
        """The stored bytes, or None (S3 `GetObject`).

        M2's preparation phase re-reads what staging wrote: it runs later and possibly in
        another process, so re-measuring the object is the only way a prepared artifact is
        a fact about the stored bytes rather than about whatever the intake process
        happened to be holding.
        """

    async def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        """Store bytes at a server-built key only if nothing is there; True if written.

        Write-once is this store's most important property, so it is one atomic operation
        rather than a read and a write a concurrent staging can interleave with.
        """

    # M3: the three operations uploads and collection need, each one S3 call.
    async def describe(self, key: str) -> tuple[int, str] | None:
        """`(size, content type)` of the stored object, or None (S3 `HeadObject`).

        What a finalizing upload checks *before* it downloads anything: an object over
        the upload's byte cap is refused without reading it into memory."""

    async def keys(self, prefix: str) -> list[str]:
        """Every key under `prefix` (S3 `ListObjectsV2`), for orphan collection."""

    async def delete(self, key: str) -> None:
        """Remove one object; absent is not an error (S3 `DeleteObject`)."""


class InMemoryObjectStore:
    """The unit-test double, and the only object store M1 ships."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, bytes, str]] = {}

    async def head(self, key: str) -> str | None:
        stored = self.objects.get(key)
        return stored[0] if stored else None

    async def get(self, key: str) -> bytes | None:
        stored = self.objects.get(key)
        return stored[1] if stored else None

    async def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = (digest_of(data), bytes(data), content_type)
        return True

    async def describe(self, key: str) -> tuple[int, str] | None:
        stored = self.objects.get(key)
        return (len(stored[1]), stored[2]) if stored else None

    async def keys(self, prefix: str) -> list[str]:
        return sorted(key for key in self.objects if key.startswith(prefix))

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

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

    #: M4: what the fetcher shows the head of a download to (`MediaFetcher.fetch` `early=`).
    #: M1 has no probe, so nothing is refused early; `MediaPreparation` refuses from the
    #: header of a clip the profile would refuse anyway.
    refuse_early = None

    def __init__(self, objects: ObjectStore, *, limits: PilotSettings = DEFAULTS,
                 fetcher: MediaFetcher | None = None, job_org=None,
                 profile_version: str = "v1", attachments=None, content=None) -> None:
        self.objects = objects
        # F2C's `ContentLifecycle` (D10's PgLifecycle in the pilot): every object this store
        # writes gets its content row FIRST (M6's collector deletes only what rows name).
        # None registers nothing - nothing is ever collected, which is the safe direction.
        self.content = content
        self.limits = limits
        self.fetcher = fetcher or MediaFetcher(limits)
        # r1 R55: where `attach` reads a job's organization. A real adapter joins the job
        # row; injected here, and an unknown job is `not_found` rather than a caller's
        # word for which tenant its refs belong to.
        self.job_org = job_org or self._no_jobs
        self.profile_version = profile_version
        # (org, handle) -> immutable ref. Keyed by tenant, so one org's handle can never
        # name, replace or shadow another org's object.
        self.refs: dict[tuple[str, str], MediaRef] = Recent()
        # MPILOT gap 2: `by_job` is this process's copy of the attach; `attachments`
        # (`attachments.PgAttachments`, D2's staged tables) is the durable one another
        # process reads - the worker, a restarted gateway. None: in process only.
        self.by_job: dict[str, tuple[MediaRef, ...]] = Recent()
        self.attachments = attachments
        # M2: what `prepare` produced, kept beside the attached sources rather than
        # replacing them. R46 allows bounded preparation retries, and a store that
        # overwrote the sources with the prepared refs would have the second attempt
        # prepare the first attempt's output - under a key derived from the profile it was
        # already prepared for.
        self.prepared_by_job: dict[str, tuple[MediaRef, ...]] = Recent()
        self.payloads: dict[str, StagedPayload] = Recent()

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

    async def _register(self, kind: ContentKind, org_id: str, key: str, digest: str,
                        size: int, *, handle: str | None = None,
                        job_id: str | None = None) -> None:
        """F2C: the content row before the object (origin `written`), so a crash between
        the two leaves a row with a persisted grace for M6's collector, never an object no
        row names. A key whose delete is pending is `content_retiring` (a 503 to retry):
        nothing is written over an object the collector is about to delete."""
        if self.content is not None:
            await self.content.register(ContentIdentity(
                org_id=org_id, kind=kind, location=ContentLocation.object_store,
                object_key=key, digest=digest, bytes=size, upload_handle=handle,
                job_id=job_id, origin=ContentOrigin.written))

    async def _write_once(self, key: str, data: bytes, content_type: str) -> None:
        """Immutable content: the same bytes twice is a no-op, different bytes under a
        key that already exists is a conflict, never a replacement."""
        if await self.objects.put_if_absent(key, data, content_type):
            return
        if await self.objects.head(key) != digest_of(data):
            raise errors.Conflict(f"an object already exists at {key} with different content")

    # --- materialization (M1's own operation, not a port one) ----------------
    async def facts(self, data: bytes, mime: str) -> tuple[str, float | None]:
        """What this store records about the bytes it is about to write: `(mime, duration)`.

        M1 records the declared type and no duration, which is exactly what a store with no
        decoder can honestly say. M2's `MediaPreparation` overrides this with a probe of the
        bytes, so the type and the duration on a `MediaRef` become measurements rather than
        the caller's word - and, because it is called **before** the object is written, media
        the profile refuses is never stored at all.
        """
        return mime, None

    async def materialize(self, org_id: str, source: str) -> MediaRef:
        """Fetch or decode one caller-supplied source **once** and store it durably.

        The only place a caller's URL is touched. The returned handle is derived from
        the content digest, so the same source materialized twice is one object and one
        ref rather than two rows pointing at the same bytes.
        """
        org_id = valid_org(org_id)          # before any outbound request is made
        if source.startswith(DATA_PREFIX):
            # M4: in a worker thread, so decoding's digest (hashlib releases the GIL) is
            # off the event loop; the base64 decode itself still holds the GIL.
            fetched, kind = await asyncio.to_thread(
                decode_data_url, source, self.limits, self.fetcher.allowed_mime), MediaKind.inline
        elif source.startswith(HTTP_PREFIXES):
            fetched, kind = await self.fetcher.fetch(source, early=self.refuse_early), \
                MediaKind.url
        else:
            raise errors.InvalidRequest("a media source must be an http(s) or data: URL")
        if len(fetched.data) > self.limits.max_media_bytes:
            raise errors.RequestTooLarge(f"{len(fetched.data)} bytes exceeds MAX_MEDIA_BYTES")
        # Measured here rather than taken from the fetcher: the digest is the object's
        # identity, its key and its handle, so it is computed from the bytes being stored.
        # M4: in a worker thread - hashlib releases the GIL, so a 64 MiB digest no longer
        # stalls every other request on this event loop (M4 evidence).
        digest = await asyncio.to_thread(digest_of, fetched.data)
        # Before the write, so a clip the profile refuses costs no object (M2).
        mime, duration_s = await self.facts(fetched.data, fetched.mime)
        ref = MediaRef(org_id=org_id, handle=media_handle(digest), kind=kind,
                       digest=digest, bytes=len(fetched.data), mime=mime,
                       storage_ref=self._key(org_id, digest, self.profile_version, "source"),
                       profile_version=self.profile_version, duration_s=duration_s)
        clash = self.refs.get((org_id, ref.handle))
        if clash is not None and clash.digest != digest:
            # The handle is a prefix of the digest, so this cannot happen by chance; if it
            # ever does (a shorter handle, a different scheme) it must not silently
            # replace the tenant's ref with one pointing at other content.
            raise errors.Conflict(f"handle {ref.handle} already names different content")
        await self._register(ContentKind.source, org_id, ref.storage_ref, digest, ref.bytes)
        await self._write_once(ref.storage_ref, fetched.data, ref.mime)
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
        for ref in request.media:
            # Every lookup below is inside this org's namespace, which is the tenant guard:
            # another org's ref, or one relabelled with this org, is not there (F2R item 4).
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
            if existing is None or existing.digest != ref.digest:
                # F2R item 4: only a ref this store materialized for this org is staged -
                # never one it did not produce, nor a real handle claiming other content.
                raise errors.NotFound(f"media {ref.handle} was not materialized for org {org_id}")
            # The **indexed** ref, never the caller's copy of it: the request's `bytes`,
            # `mime`, `duration_s` and `storage_ref` are claims (review B4/S03).
            resolved.append(existing)
        payload = codec.canonical_bytes(request)
        # A server-built key from server-known identity; `request.payload_ref` is not read,
        # because a caller-named path is exactly what a media store must never accept.
        key = f"payloads/{valid_org(org_id)}/{request.request_id}.json"
        digest = digest_of(payload)
        # The envelope serves one job: once admitted, that job keeps it (F2C); a request
        # refused after staging leaves a row the collector removes after its grace.
        await self._register(ContentKind.payload, org_id, key, digest, len(payload),
                             job_id=request.request_id)
        await self._write_once(key, payload, "application/json")
        self.payloads[request.request_id] = StagedPayload(ref=key, digest=digest,
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
            indexed = await self._staged_ref(org_id, ref)
            if indexed is None:
                raise errors.NotFound(f"media {ref.handle} was not staged for org {org_id}")
            owned.append(indexed)
        owned = tuple(owned)
        if len({ref.handle for ref in owned}) != len(owned):
            raise errors.InvalidRequest("an attach names each media ref once")
        # Write-once (MPILOT review PAR-1): the same refs again are a no-op - the relay's
        # same-key retry re-attaches them - and any other binding is a conflict.
        bound = self.by_job.get(job_id)
        if bound is not None and bound != owned:
            raise errors.Conflict(f"job {job_id} is already attached to other media")
        if self.attachments is not None:            # durable first (MPILOT gap 2)
            await self.attachments.put(job_id, owned)
        self.by_job[job_id] = owned

    async def _staged_ref(self, org_id: str, ref: MediaRef) -> MediaRef | None:
        """This store's own record of `ref` for `org_id` (R82), or None."""
        indexed = self.refs.get((org_id, ref.handle))
        return indexed if indexed is not None and indexed.digest == ref.digest else None

    async def attached(self, job_id: str) -> tuple[MediaRef, ...] | None:
        """The refs bound to the job, from this process or the durable record; None if
        none were. ponytail: the durable record has no row for an attach of no media (0003's
        `job_media` names refs), so another process answers None for a text job - which only
        a preparer outside the attaching process would ask; 0019 (a zero-ref marker) if one
        ever runs there."""
        refs = self.by_job.get(job_id)
        if refs is None and self.attachments is not None:
            refs = await self.attachments.get(job_id)
        return refs

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
