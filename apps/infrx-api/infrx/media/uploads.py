"""M3: owned uploads - the third way media reaches a job, beside a URL and a `data:` URL.

The lifecycle is `create_upload` -> the client's bytes land at a server-built destination
(`put_upload`, or the object store behind a constrained destination) -> `finalize_upload`
-> the finalized ref is used like any staged ref (`stage` resolves it, `attach` binds it).

The rules, each of which has a test and a mutant in `tests/m`:

* **constrained create.** The caller states a byte cap, an exact size, the accepted types
  and a digest, and every one is validated here: an unknown constraint is a rule nobody
  would enforce, so it is refused rather than ignored. The handle is random (ids.py) and
  the destination is `infrx-upload:upl_<id>` - no org qualifier (R61(1)); the org comes
  from the authenticated key on every later call.
* **finalize once.** Completion re-measures the bytes that actually arrived: size first
  from the HEAD (an oversize object is refused without being downloaded), then from the
  bytes themselves, then the digest, then the container through `facts()` (M2's probe).
  The verified bytes are copied to the same content-addressed, write-once `source` key a
  materialized URL gets, so `prepare` finds an upload exactly where it finds any source
  (M2 limit 10). A retry of a completed upload returns the same ref; a retry after the
  destination was overwritten with other bytes is a conflict, never a second object; a
  refused upload stays refused.
* **tenant scope everywhere.** An upload is found by `(org, handle)`: another org's handle
  is `not_found`, never `forbidden`.

State is in process (`self.uploads`); D2 owns the durable rows (integration request 1 in
the M3 evidence names the columns).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..contracts import errors
from ..contracts.ids import UPLOAD_HANDLE_RE, new_upload_handle
from ..contracts.records import MediaKind, MediaRef, UploadState
from ..contracts.wire import UploadCreated
from .fetch import digest_of
from .prepare import MediaPreparation
from .store import valid_digest, valid_org

UPLOAD_REF_SCHEME = "infrx-upload:"          # R61(1): `infrx-upload:upl_<id>`, nothing else
UPLOAD_KEY_PREFIX = "uploads/"               # where uploaded bytes wait for completion
CONSTRAINTS = frozenset({"max_bytes", "bytes", "accepted_mime", "digest"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _count(value: object, name: str, upper: int) -> int:
    """A caller-supplied byte count: an int in 1..upper, or a typed 400. `True`, `"4096"`
    and `1.5` are not byte counts."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise errors.InvalidRequest(f"{name} must be an integer in 1..{upper}")
    return value


@dataclass
class Upload:
    """One upload's record: what was promised at create and what finalize established."""

    handle: str
    org_id: str
    max_bytes: int
    accepted_mime: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    size: int | None = None                  # the exact size the client declared
    digest: str | None = None                # the digest the client declared
    state: UploadState = UploadState.created
    ref: MediaRef | None = None              # set once, at finalize
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)


class MediaUploads(MediaPreparation):
    """`ports.MediaStore` complete: M1's staging, M2's preparation and M3's uploads."""

    def __init__(self, objects, *, now=_utc_now, new_handle=new_upload_handle,
                 **preparation) -> None:
        super().__init__(objects, **preparation)
        self.now = now
        self.new_handle = new_handle
        self.uploads: dict[str, Upload] = {}
        # Object key -> when it was last used (staged, attached, finalized). `gc.py` reads
        # it: nothing used within the grace period is collected.
        self.idle_since: dict[str, datetime] = {}

    def upload_key(self, org_id: str, handle: str) -> str:
        if not isinstance(handle, str) or not UPLOAD_HANDLE_RE.fullmatch(handle):
            raise errors.InvalidRequest("an upload handle must be upl_ + 22..64 [A-Za-z0-9_-]")
        return f"{UPLOAD_KEY_PREFIX}{valid_org(org_id)}/{handle}"

    def _owned(self, org_id: str, handle: object) -> Upload:
        upload = self.uploads.get(handle) if isinstance(handle, str) else None
        if upload is None or upload.org_id != org_id:
            raise errors.NotFound(f"no upload {handle} owned by org {org_id}")
        return upload

    def _touch(self, refs) -> None:
        now = self.now()
        for ref in refs:
            self.idle_since[ref.storage_ref] = now

    # --- create ---------------------------------------------------------------
    async def create_upload(self, org_id: str, constraints: dict) -> dict:
        org_id = valid_org(org_id)
        if not isinstance(constraints, dict):
            raise errors.InvalidRequest("upload constraints must be an object")
        unknown = set(constraints) - CONSTRAINTS
        if unknown:
            raise errors.InvalidRequest(f"unknown upload constraints: {sorted(map(str, unknown))}")
        max_bytes = _count(constraints.get("max_bytes", self.limits.max_media_bytes),
                           "max_bytes", self.limits.max_media_bytes)
        size = constraints.get("bytes")
        if size is not None:
            size = _count(size, "bytes", max_bytes)
        allowed = self.fetcher.allowed_mime
        mimes = constraints.get("accepted_mime", tuple(sorted(allowed)))
        # A string is not a list: `tuple("video/mp4")` is a tuple of characters.
        if not isinstance(mimes, (list, tuple)) or not mimes \
                or not all(isinstance(mime, str) and mime in allowed for mime in mimes):
            raise errors.InvalidRequest("accepted_mime must be a nonempty list of allowed "
                                        "media types")
        digest = constraints.get("digest")
        if digest is not None:
            valid_digest(digest)
        handle = self.new_handle()
        if handle in self.uploads or not UPLOAD_HANDLE_RE.fullmatch(handle):
            raise errors.InternalError("the upload handle source produced an unusable handle")
        now = self.now()
        upload = Upload(handle=handle, org_id=org_id, max_bytes=max_bytes,
                        accepted_mime=tuple(mimes), created_at=now,
                        expires_at=now + timedelta(seconds=self.limits.processing_cache_ttl_s),
                        size=size, digest=digest)
        self.uploads[handle] = upload
        return UploadCreated(upload_handle=handle, destination_ref=UPLOAD_REF_SCHEME + handle,
                             max_bytes=max_bytes, accepted_mime=upload.accepted_mime,
                             state=upload.state, expires_at=upload.expires_at).model_dump()

    def _still_open(self, upload: Upload) -> None:
        if upload.state is not UploadState.created:
            raise errors.Conflict(f"upload {upload.handle} is {upload.state}")
        if self.now() >= upload.expires_at:
            upload.state = UploadState.expired
            raise errors.UploadExpired("the upload window closed before completion")

    # --- the bytes (G4U's HTTP adapter calls this with the request body) ---------------
    async def put_upload(self, org_id: str, handle: str, data: bytes, content_type: str) -> None:
        """Store the client's bytes at the upload's destination. Write-once: the same bytes
        again is a no-op, other bytes are a conflict, and nothing is accepted once the
        upload is finalized, refused or expired."""
        upload = self._owned(valid_org(org_id), handle)
        self._still_open(upload)
        if len(data) > upload.max_bytes:
            raise errors.RequestTooLarge(f"{len(data)} bytes over the upload's {upload.max_bytes}")
        await self._write_once(self.upload_key(org_id, handle), data, content_type)

    # --- finalize -------------------------------------------------------------
    async def finalize_upload(self, org_id: str, upload_handle: str) -> MediaRef:
        org_id = valid_org(org_id)
        upload = self._owned(org_id, upload_handle)
        # One completion at a time per upload: a concurrent retry waits and then takes
        # the idempotent branch, instead of verifying and copying a second time.
        async with upload.lock:
            return await self._finalize(upload)

    async def _finalize(self, upload: Upload) -> MediaRef:
        key = self.upload_key(upload.org_id, upload.handle)
        if upload.state is UploadState.finalized:
            # Idempotent, and only for the bytes it was finalized with: a destination
            # overwritten since then is a conflict, never a second object.
            if await self.objects.head(key) not in (None, upload.ref.digest):
                raise errors.Conflict(f"upload {upload.handle} was finalized with other bytes")
            return upload.ref
        self._still_open(upload)
        described = await self.objects.describe(key)
        if described is None:
            # Nothing arrived yet: not a refusal, the client may still upload.
            raise errors.InvalidRequest("no object was uploaded to the issued destination")
        size, declared_mime = described
        if size > upload.max_bytes:                  # refused before it is downloaded
            raise self._abort(upload, errors.RequestTooLarge(
                f"uploaded {size} bytes over {upload.max_bytes}"))
        data = await self.objects.get(key)
        if data is None:
            raise errors.InvalidRequest("no object was uploaded to the issued destination")
        # The bytes are authoritative, not the HEAD that described them.
        if len(data) > upload.max_bytes:
            raise self._abort(upload, errors.RequestTooLarge(
                f"uploaded {len(data)} bytes over {upload.max_bytes}"))
        if upload.size is not None and len(data) != upload.size:
            raise self._abort(upload, errors.InvalidRequest(
                f"uploaded {len(data)} bytes, declared {upload.size}"))
        digest = digest_of(data)
        if upload.digest is not None and digest != upload.digest:
            raise self._abort(upload, errors.UnsupportedMedia(
                "the uploaded bytes do not match the declared digest"))
        try:
            # The container, not the client's Content-Type: M2's probe when preparation is
            # configured. A probe that timed out (platform-side) leaves the upload open.
            mime, duration_s = await self.facts(data, declared_mime)
        except errors.InvalidRequest as refused:
            raise self._abort(upload, refused)
        if mime not in upload.accepted_mime:
            raise self._abort(upload, errors.UnsupportedMedia(f"{mime} is not an accepted type"))
        ref = MediaRef(org_id=upload.org_id, handle=upload.handle, kind=MediaKind.upload,
                       digest=digest, bytes=len(data), mime=mime,
                       storage_ref=self._key(upload.org_id, digest, self.profile_version,
                                             "source"),
                       profile_version=self.profile_version, duration_s=duration_s)
        existing = self.refs.get((upload.org_id, upload.handle))
        if existing is not None and existing.digest != digest:
            # Immutable within the tenant too: completing an upload never replaces what
            # its handle already names.
            raise self._abort(upload, errors.Conflict(
                f"handle {upload.handle} already names different content"))
        await self._write_once(ref.storage_ref, data, mime)
        self.refs[(upload.org_id, upload.handle)] = ref
        upload.state, upload.ref = UploadState.finalized, ref
        self._touch((ref,))
        return ref

    @staticmethod
    def _abort(upload: Upload, error: errors.DomainError) -> errors.DomainError:
        """A failed check is final: a second finalize is not a second chance at it."""
        upload.state = UploadState.aborted
        return error

    # --- use ------------------------------------------------------------------
    async def resolve_owned(self, org_id: str, ref: str) -> MediaRef:
        media = await super().resolve_owned(org_id, ref)     # another org's: not_found
        # Scoped by (org, handle): another org's upload state is never answered (review).
        upload = self.uploads.get(ref)
        if upload is not None and upload.org_id == org_id \
                and upload.state is not UploadState.finalized:
            raise errors.InvalidRequest(f"upload {ref} is {upload.state}, not finalized")
        if media.kind is MediaKind.upload \
                and await self.objects.head(media.storage_ref) != media.digest:
            # Collected, or replaced behind the store: a job must not run on either.
            raise errors.NotFound(f"the object for upload {ref} is gone")
        return media

    async def stage(self, org_id, request):
        refs = await super().stage(org_id, request)
        self._touch(refs)
        return refs

    async def attach(self, job_id, refs) -> None:
        await super().attach(job_id, refs)
        self._touch(self.by_job[job_id])
