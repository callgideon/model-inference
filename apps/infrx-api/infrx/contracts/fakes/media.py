"""In-memory MediaStore. Immutable, content addressed, tenant scoped.

The security properties M must keep are encoded here: a caller never names a
storage path, a cross-tenant reference is a 404, oversize is refused before the
bytes are used, and a finalized upload's digest is verified rather than trusted.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .. import errors
from ..limits import DEFAULTS, PilotSettings
from ..records import MediaKind, MediaRef, NormalizedRequest, UploadState
from .support import FailurePlan, FakeClock, SequentialIds, failure_hooks


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class _Upload:
    handle: str
    org_id: str
    max_bytes: int
    accepted_mime: tuple[str, ...]
    expires_at: datetime
    state: UploadState = UploadState.created
    data: bytes | None = None
    mime: str | None = None
    declared_digest: str | None = None       # what the client said it would upload


class FakeMediaStore:
    """`ports.MediaStore`."""

    def __init__(self, clock: FakeClock | None = None, ids: SequentialIds | None = None, *,
                 limits: PilotSettings = DEFAULTS, failures: FailurePlan | None = None) -> None:
        self.clock = clock or FakeClock()
        self.ids = ids or SequentialIds()
        self.limits = limits
        self.failures = failure_hooks(failures)
        self.uploads: dict[str, _Upload] = {}
        # (org, handle) -> immutable ref. Keyed by tenant so one org's handle can
        # never name, replace or shadow another org's object.
        self.objects: dict[tuple[str, str], MediaRef] = {}
        self.by_job: dict[str, tuple[MediaRef, ...]] = {}

    # --- test hooks (not part of the port) -----------------------------------
    def put_object(self, upload_handle: str, data: bytes, mime: str = "video/mp4") -> None:
        """What the client actually uploaded to the issued destination."""
        upload = self.uploads[upload_handle]
        upload.data, upload.mime = data, mime

    # --- port ---------------------------------------------------------------
    async def attach(self, job_id: str, org_id: str, refs: tuple[MediaRef, ...]) -> None:
        """r1 R46: bind staged refs to an admitted job, as the job row does in
        PostgreSQL. A port operation, not a test hook: `prepare` cannot work without
        it, so leaving it out of the port left M free to invent its own way in.

        r1 R52: **every ref must belong to the job's organization.** ORG_B's refs used to
        attach happily to an ORG_A job and were only caught much later, by `prepared`,
        after `prepare` had already transcoded them and written objects under ORG_A's
        prefix. The tenant check belongs at the write, not two phases downstream.
        """
        self.failures.before("attach")
        for ref in refs:
            if ref.org_id != org_id:
                raise errors.NotFound("media attached to a job must belong to its org")
        self.by_job[job_id] = tuple(refs)

    async def stage(self, org_id: str, request: NormalizedRequest) -> tuple[MediaRef, ...]:
        """Durable, immutable staging before acceptance. The storage key is built from
        the tenant, the source digest and the profile version; the caller has no say
        in it.

        All or nothing (02: "a staging failure creates no job or hold"): every
        reference is validated **and resolved** first, the objects to write are
        prepared in memory, and only then does anything become visible. An upload that
        cannot be resolved therefore leaves no inline sibling staged behind, so a
        client that corrects the bad reference and retries does not find half its
        request already stored under a handle it can no longer change.
        """
        self.failures.before("stage")
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
            existing = self.objects.get((org_id, ref.handle))
            if ref.kind is MediaKind.upload:
                # Resolution can fail (unknown handle, unfinalized upload, digest
                # mismatch), so it belongs in this pass, before anything is written.
                owned = await self.resolve_owned(org_id, ref.handle)
                if owned.digest != ref.digest:
                    raise errors.UnsupportedMedia("upload digest does not match the reference")
                resolved.append(owned)
                continue
            if existing is not None:
                # Staged and finalized content is immutable: the same handle keeps the
                # object it already has, and different content is a conflict.
                if existing.digest != ref.digest:
                    raise errors.Conflict(
                        f"media handle {ref.handle} already holds different content")
                resolved.append(existing)
                continue
            stored = ref.model_copy(update={
                "storage_ref": self._key(org_id, ref.digest, ref.profile_version, "source")})
            clash = pending.get((org_id, stored.handle))
            if clash is not None and clash.digest != stored.digest:
                # The same handle twice in one request, with different content: last-wins
                # would silently stage one and hand the job the other's digest.
                raise errors.InvalidRequest(
                    f"media handle {ref.handle} appears twice with different content")
            pending[(org_id, stored.handle)] = stored
            resolved.append(stored)
        # One visible step: nothing above wrote to `self.objects`.
        self.objects.update(pending)
        return tuple(resolved)

    @staticmethod
    def _key(org_id: str, digest: str, profile_version: str, part: str) -> str:
        # The tenant source digest plus the profile version namespaces the cache,
        # so two orgs with identical bytes never share an object.
        return f"media/{org_id}/{profile_version}/{digest.split(':')[1][:16]}/{part}"

    async def prepare(self, job_id: str, profile: str) -> tuple[MediaRef, ...]:
        """r1 R46: addressed by `job_id`, like every internal operation."""
        self.failures.before("prepare")
        sources = self.by_job.get(job_id)
        if sources is None:
            raise errors.NotFound(f"no staged media for job {job_id}")
        prepared = tuple(
            ref.model_copy(update={"profile_version": profile,
                                   "storage_ref": self._key(ref.org_id, ref.digest, profile,
                                                            "prepared")})
            for ref in sources)
        self.by_job[job_id] = prepared
        return prepared

    async def create_upload(self, org_id: str, constraints: dict[str, object]) -> dict[str, object]:
        self.failures.before("create_upload")
        raw = constraints.get("max_bytes", self.limits.max_media_bytes)
        # A caller-supplied bound is a trust boundary: `"abc"`, `1.5` or `nan` must be
        # a typed 400, not a ValueError escaping to a 500.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise errors.InvalidRequest("max_bytes must be an integer number of bytes")
        max_bytes = raw
        if max_bytes <= 0 or max_bytes > self.limits.max_media_bytes:
            raise errors.InvalidRequest(f"max_bytes must be in 1..{self.limits.max_media_bytes}")
        unknown = set(constraints) - {"max_bytes", "accepted_mime", "digest"}
        if unknown:
            # A constraint the store does not understand is a caller expecting a rule
            # nobody enforces; silently ignoring it is how a limit goes missing.
            raise errors.InvalidRequest(f"unknown upload constraints: {sorted(unknown)}")
        raw_mimes = constraints.get("accepted_mime", ("video/mp4",))
        if not isinstance(raw_mimes, (list, tuple, set, frozenset, str, bytes)):
            # `5` is not an allow-list; `tuple(5)` would be a TypeError out of a port.
            raise errors.InvalidRequest("accepted_mime must be a list of media types")
        if isinstance(raw_mimes, (str, bytes)):
            # `tuple("video/mp4")` is a tuple of characters, which would accept nothing
            # and look like an allow-list. A single type must be a one-item list.
            raise errors.InvalidRequest("accepted_mime must be a list of media types")
        mimes = tuple(raw_mimes)
        if not mimes or not all(isinstance(mime, str) and mime.strip() for mime in mimes):
            raise errors.InvalidRequest("accepted_mime must be a nonempty list of media types")
        declared = constraints.get("digest")
        if declared is not None and not (isinstance(declared, str)
                                         and declared.startswith("sha256:") and len(declared) == 71):
            raise errors.InvalidRequest("a declared digest must be sha256:<64 hex digits>")
        handle = self.ids.upload_handle()
        upload = _Upload(handle=handle, org_id=org_id, max_bytes=max_bytes, accepted_mime=mimes,
                         expires_at=self.clock.at(self.limits.processing_cache_ttl_s),
                         declared_digest=declared)
        self.uploads[handle] = upload
        return {"upload_handle": handle,
                # A constrained server-issued destination, never a caller-shaped key.
                "destination_ref": f"infrx-upload:{org_id}:{handle}",
                "max_bytes": max_bytes, "accepted_mime": mimes,
                "state": upload.state, "expires_at": upload.expires_at}

    async def finalize_upload(self, org_id: str, upload_handle: str) -> MediaRef:
        self.failures.before("finalize_upload")
        upload = self.uploads.get(upload_handle)
        if upload is None or upload.org_id != org_id:
            raise errors.NotFound(f"no upload {upload_handle} owned by org {org_id}")
        if upload.state is UploadState.finalized:
            return self.objects[(org_id, upload_handle)]   # idempotent completion
        if upload.state in (UploadState.aborted, UploadState.expired):
            # A refused upload stays refused: re-finalizing after an oversize or
            # unsupported body would be a second chance at the same check.
            raise errors.Conflict(f"upload {upload_handle} is {upload.state}")
        if self.clock.now() >= upload.expires_at:
            upload.state = UploadState.expired
            raise errors.UploadExpired("the upload window closed before completion")
        if not upload.data:
            raise errors.InvalidRequest("no object was uploaded to the issued destination")
        if len(upload.data) > upload.max_bytes:
            upload.state = UploadState.aborted
            raise errors.RequestTooLarge(f"uploaded {len(upload.data)} bytes over {upload.max_bytes}")
        if upload.mime not in upload.accepted_mime:
            upload.state = UploadState.aborted
            raise errors.UnsupportedMedia(f"{upload.mime} is not an accepted type")
        digest = digest_of(upload.data)
        if upload.declared_digest is not None and upload.declared_digest != digest:
            # The client said what it would upload; the store checked, as 01 requires
            # ("completion verifies object metadata/checksum").
            upload.state = UploadState.aborted
            raise errors.UnsupportedMedia("the uploaded bytes do not match the declared digest")
        existing = self.objects.get((org_id, upload_handle))
        if existing is not None and existing.digest != digest:
            # Immutable within the tenant too: finalizing must not replace an object
            # already staged under this handle, or the owner's own content vanishes.
            raise errors.Conflict(f"handle {upload_handle} already holds different content")
        ref = MediaRef(org_id=org_id, handle=upload_handle, kind=MediaKind.upload, digest=digest,
                       bytes=len(upload.data), mime=upload.mime,
                       storage_ref=self._key(org_id, digest, "v1", "source"))
        upload.state = UploadState.finalized
        self.objects[(org_id, upload_handle)] = ref       # immutable from here on
        return ref

    async def resolve_owned(self, org_id: str, ref: str) -> MediaRef:
        # Keyed by tenant: another org's handle simply is not in this org's namespace,
        # which is the single guard rather than a second comparison after the lookup.
        media = self.objects.get((org_id, ref))
        if media is None:
            raise errors.NotFound(f"no media {ref} owned by org {org_id}")
        upload = self.uploads.get(ref)
        if upload is not None and upload.state is not UploadState.finalized:
            raise errors.InvalidRequest(f"upload {ref} is {upload.state}, not finalized")
        return media
