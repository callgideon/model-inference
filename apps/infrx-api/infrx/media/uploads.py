"""M3 + M5: owned uploads - the third way media reaches a job, beside a URL and a `data:` URL.

The lifecycle is `create_upload` -> the client's bytes at a server-built destination
(`put_upload`, G4U's authenticated same-host PUT) -> `finalize_upload` -> the finalized ref is
used like any staged ref (`stage` resolves it, `attach` binds it).

**M5 (RV-02): the ticket is durable.** Its owner, constraints, window, the bytes that
arrived and its completion live in F2C's `UploadRepository` (`contracts.v2.lifecycle`) -
D10's PostgreSQL in the pilot - and nothing about an upload is remembered in this process.
Create, PUT, complete and use may each land on a different gateway process, and finalized
tickets cost this process no memory. `ProcessUploads`, the authority M3 had, is the default
only where nothing durable is composed (unit and route suites), and the executable negative
control of the cross-process suite (`tests/m/test_upload_restart.py`).

The rules, each of which has a test and a mutant in `tests/m`:

* **constrained create.** The caller states a byte cap, an exact size, the accepted types
  and a digest; F2C's `UploadConstraints.parse` validates every one against the deployment's
  bounds, and an unknown one is refused, never ignored.
  The repository mints the opaque handle and the window; the destination is
  `infrx-upload:upl_<id>` - no org qualifier (R61(1)); the org is the key's on every call.
* **the receipt before the bytes.** `put_upload` measures the body and has the repository
  record it (owner, open window, the ticket's cap, one set of bytes per handle) *before*
  the destination is written, write-once. A foreign, closed or oversize PUT writes nothing;
  a retry after a lost acknowledgement is the same receipt and the same object: a no-op.
* **verify, write, then commit.** Completion re-measures what is at the destination - size,
  digest, then the container through `facts()` (M2's probe) - and never takes a declared
  MIME, duration or digest as a fact. The verified bytes go to the content-addressed,
  write-once `source` key *before* the repository commits the completion, so a crash
  between the two leaves a retry that finishes (the write is a no-op), never a finalized
  ticket naming no object. A failed check aborts the ticket durably, and an aborted ticket
  stays aborted. Object I/O runs between short repository calls, never inside one, and the
  downloads of concurrent completions share the preparation pool's bound (`gate`).
* **use.** A handle resolves through the repository - the caller's organization's, inside
  its window (R99(b)), finalized - and its object must still carry the finalized digest.
  An admitted job's attach checks the object, not the window: the ticket authorized the
  ref before admission, and a job outlives the window of the upload it runs on.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from ..contracts import errors
from ..contracts.ids import UPLOAD_HANDLE_RE, new_upload_handle
from ..contracts.records import MediaKind, MediaRef, UploadState
from ..contracts.v2.lifecycle import (DESTINATION_SCHEME, UPLOAD_ABORT_REASONS,
                                      ContentIdentity, ContentKind, ContentLocation,
                                      ContentOrigin, FinalizedSource, LifecycleRefusal as R,
                                      UploadConstraints, UploadReceipt, UploadTicket,
                                      refusal_of, refuse)
from ..contracts.wire import UploadCreated
from .fetch import digest_of
from .prepare import MediaPreparation
from .store import DIGEST_RE, valid_org

UPLOAD_REF_SCHEME = DESTINATION_SCHEME       # R61(1): `infrx-upload:upl_<id>`, nothing else
UPLOAD_KEY_PREFIX = "uploads/"               # where uploaded bytes wait for completion


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProcessUploads:
    """F2C's `UploadRepository` in THIS process's memory: the authority M3 had (RV-02).

    For what composes no database - the unit and route suites - and the negative control of
    the cross-process suite: another process, or this one restarted, sees none of it. The
    pilot composes D10's PostgreSQL adapter (M5 wiring request 1). The port's refusals, in
    the port's order; time is the injected `now`."""

    def __init__(self, *, now, new_handle, window_s: float) -> None:
        self.now, self.new_handle, self.window_s = now, new_handle, window_s
        self.records: dict[str, UploadTicket] = {}

    def _save(self, ticket: UploadTicket, **changes) -> UploadTicket:
        # Revalidated, never `model_copy(update=)`: the record's own rules hold on every write.
        ticket = UploadTicket.model_validate({**ticket.model_dump(), **changes})
        self.records[ticket.upload_handle] = ticket
        return ticket

    def _owned(self, org_id: str, handle: object) -> UploadTicket:
        ticket = self.records.get(handle) if isinstance(handle, str) else None
        if ticket is None or ticket.org_id != org_id:
            raise refuse(R.not_found, f"no upload {handle} owned by org {org_id}")
        return ticket

    def _open(self, ticket: UploadTicket) -> None:
        if ticket.state is not UploadState.created:
            raise refuse(R.upload_not_open, f"upload {ticket.upload_handle} is {ticket.state}")
        if self.now() >= ticket.expires_at:
            raise refuse(R.upload_expired, "the upload window closed before completion")

    async def create(self, org_id: str, constraints: UploadConstraints) -> UploadTicket:
        handle, now = self.new_handle(), self.now()
        if not isinstance(handle, str) or not UPLOAD_HANDLE_RE.fullmatch(handle) \
                or handle in self.records:
            raise errors.InternalError("the upload handle source produced an unusable handle")
        return self._save(UploadTicket(
            upload_handle=handle, org_id=org_id, destination_ref=DESTINATION_SCHEME + handle,
            constraints=constraints, state=UploadState.created, created_at=now,
            expires_at=now + timedelta(seconds=self.window_s)))

    async def acknowledge_put(self, org_id: str, upload_handle: str, *, bytes: int,
                              digest: str) -> UploadTicket:
        ticket = self._owned(org_id, upload_handle)
        self._open(ticket)
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise errors.InvalidRequest("a receipt's digest is sha256:<64 hex>")
        if bytes > ticket.constraints.max_bytes:
            raise refuse(R.too_large, f"{bytes} bytes over the upload's "
                                      f"{ticket.constraints.max_bytes}")
        if ticket.received is not None:
            if (ticket.received.bytes, ticket.received.digest) != (bytes, digest):
                raise refuse(R.bytes_changed, "the handle already received other bytes")
            return ticket
        return self._save(ticket, received=UploadReceipt(bytes=bytes, digest=digest,
                                                         received_at=self.now()))

    async def complete(self, org_id: str, upload_handle: str, source: MediaRef) -> UploadTicket:
        ticket = self._owned(org_id, upload_handle)
        if (source.org_id, source.handle, source.kind) != (org_id, upload_handle,
                                                           MediaKind.upload):
            raise refuse(R.not_found, "the source is not this ticket's")
        done = ticket.finalized
        if done is not None:
            if (done.digest, done.bytes, done.mime) != (source.digest, source.bytes, source.mime):
                raise refuse(R.bytes_changed, f"upload {upload_handle} was finalized with "
                                              "other bytes")
            return ticket
        self._open(ticket)
        received, c = ticket.received, ticket.constraints
        if source.duration_s is None:
            raise errors.InvalidRequest("a finalized upload carries its measured duration")
        if received is None:
            raise refuse(R.nothing_received, "no bytes reached the destination")
        if (source.digest, source.bytes) != (received.digest, received.bytes):
            raise refuse(R.bytes_changed, "the destination changed since it was received")
        failed = (R.size_mismatch if c.bytes is not None and source.bytes != c.bytes
                  else R.digest_mismatch if c.digest is not None and source.digest != c.digest
                  else R.mime_not_accepted if source.mime not in c.accepted_mime else None)
        if failed is not None:
            self._save(ticket, state=UploadState.aborted, refusal=failed)
            raise refuse(failed, "a constraint failed; the upload is aborted")
        return self._save(ticket, state=UploadState.finalized, finalized=FinalizedSource(
            # no content table here: an id stable for the object key, as a content row's is
            content_id=str(uuid.UUID(bytes=hashlib.sha256(source.storage_ref.encode())
                                     .digest()[:16], version=4)), generation=1,
            digest=source.digest, bytes=source.bytes, mime=source.mime,
            profile_version=source.profile_version, duration_s=source.duration_s,
            finalized_at=self.now()))

    async def abort(self, org_id: str, upload_handle: str, refusal: R) -> UploadTicket:
        if refusal not in UPLOAD_ABORT_REASONS:
            raise errors.InvalidRequest("a ticket records only a public upload refusal")
        ticket = self._owned(org_id, upload_handle)
        if ticket.state is UploadState.aborted:
            return ticket
        if ticket.state is not UploadState.created or self.now() >= ticket.expires_at:
            raise refuse(R.upload_not_open, f"upload {upload_handle} is {ticket.state} or "
                                            "past its window")
        return self._save(ticket, state=UploadState.aborted, refusal=R(refusal))

    async def resolve(self, org_id: str, upload_handle: str) -> UploadTicket:
        ticket = self._owned(org_id, upload_handle)
        if self.now() >= ticket.expires_at:
            raise refuse(R.upload_expired, "the upload window has expired")
        if ticket.state is not UploadState.finalized:
            raise refuse(R.upload_not_finalized, f"upload {upload_handle} is {ticket.state}")
        return ticket

    async def expire(self, limit: int) -> int:
        now = self.now()
        due = sorted((t for t in self.records.values()
                      if t.state is UploadState.created and now >= t.expires_at),
                     key=lambda t: (t.expires_at, t.upload_handle))[:limit]
        for ticket in due:
            self._save(ticket, state=UploadState.expired)
        return len(due)


class MediaUploads(MediaPreparation):
    """`ports.MediaStore` complete: M1's staging, M2's preparation and M3/M5's uploads.
    `uploads` is the ticket authority (an `UploadRepository`); None is `ProcessUploads`.
    `content` (a `ContentLifecycle`, D10's same adapter in the pilot) gets a row for each
    object before it is written; None registers nothing (M3's collector, in process)."""

    def __init__(self, objects, *, uploads=None, content=None, now=_utc_now,
                 new_handle=new_upload_handle, **preparation) -> None:
        super().__init__(objects, **preparation)
        self.now = now
        self.new_handle = new_handle
        self.content = content
        # Read through `self` on every call, so a case that moves `now` moves the window.
        self.tickets = uploads if uploads is not None else ProcessUploads(
            now=lambda: self.now(), new_handle=lambda: self.new_handle(),
            window_s=self.limits.processing_cache_ttl_s)
        # Object key -> when it was last used (staged, attached, re-finalized). `gc.py` reads
        # it: nothing used within the grace period is collected.
        self.idle_since: dict[str, datetime] = {}
        # (org, handle) -> the completion running in this process; an entry leaves when its
        # completion ends, so this holds what is in flight and nothing else.
        self._finalizing: dict[tuple[str, str], asyncio.Future] = {}

    @property
    def uploads(self) -> dict[str, UploadTicket]:
        """A process-local repository's tickets (tests; the collector's destination sweep).
        A durable repository keeps none in process, so it has no such view."""
        return self.tickets.records

    def upload_key(self, org_id: str, handle: str) -> str:
        if not isinstance(handle, str) or not UPLOAD_HANDLE_RE.fullmatch(handle):
            raise errors.InvalidRequest("an upload handle must be upl_ + 22..64 [A-Za-z0-9_-]")
        return f"{UPLOAD_KEY_PREFIX}{valid_org(org_id)}/{handle}"

    def _destination(self, org_id: str, handle: object) -> str:
        """The destination of a well-formed handle; any other handle is the unknown one's
        `not_found`, never a grammar error that describes what a handle looks like."""
        if not isinstance(handle, str) or not UPLOAD_HANDLE_RE.fullmatch(handle):
            raise errors.NotFound(f"no such upload owned by org {org_id}")
        return self.upload_key(org_id, handle)

    async def _register(self, kind: ContentKind, org_id: str, key: str, digest: str,
                        size: int, handle: str | None = None) -> None:
        """F2C: the content row before the object (origin `written`), so a crash between
        the two leaves a row with a persisted grace for M6's collector, never an object no
        row names. A key whose delete is pending is `content_retiring` (a 503 to retry)."""
        if self.content is not None:
            await self.content.register(ContentIdentity(
                org_id=org_id, kind=kind, location=ContentLocation.object_store,
                object_key=key, digest=digest, bytes=size, upload_handle=handle,
                origin=ContentOrigin.written))

    def _touch(self, refs) -> None:
        now = self.now()
        for ref in refs:
            self.idle_since[ref.storage_ref] = now

    # --- create ---------------------------------------------------------------
    async def create_upload(self, org_id: str, constraints: dict) -> dict:
        org_id = valid_org(org_id)
        # F2C's one trust boundary: the four names, each validated, the deployment's bounds
        # for what is absent. A null cap or type list is a value nobody would enforce, not an
        # absent one (the v1 mediastore conformance), so it is refused before the parse.
        if isinstance(constraints, dict) and any(
                constraints.get(name, ...) is None for name in ("max_bytes", "accepted_mime")):
            raise errors.InvalidRequest("max_bytes and accepted_mime are never null")
        checked = UploadConstraints.parse(constraints,
                                          max_media_bytes=self.limits.max_media_bytes,
                                          allowed_mime=frozenset(self.fetcher.allowed_mime))
        ticket = await self.tickets.create(org_id, checked)
        return UploadCreated(upload_handle=ticket.upload_handle,
                             destination_ref=UPLOAD_REF_SCHEME + ticket.upload_handle,
                             max_bytes=ticket.constraints.max_bytes,
                             accepted_mime=ticket.constraints.accepted_mime,
                             state=ticket.state, expires_at=ticket.expires_at).model_dump()

    # --- the bytes (G4U's HTTP adapter calls this with the request body) ---------------
    async def put_upload(self, org_id: str, handle: str, data: bytes, content_type: str) -> None:
        """Store the client's bytes at the upload's destination. The receipt first - owner,
        open window, cap and one set of bytes per handle, in the repository - so a refused
        PUT writes nothing; then the destination, write-once, so the same bytes again (a
        retry after a lost acknowledgement) are a no-op."""
        key = self._destination(valid_org(org_id), handle)
        digest = await asyncio.to_thread(digest_of, data)
        await self.tickets.acknowledge_put(org_id, handle, bytes=len(data), digest=digest)
        await self._register(ContentKind.upload_destination, org_id, key, digest, len(data),
                             handle)
        await self._write_once(key, data, content_type)

    # --- finalize -------------------------------------------------------------
    async def finalize_upload(self, org_id: str, upload_handle: str) -> MediaRef:
        key = self._destination(valid_org(org_id), upload_handle)
        # One completion at a time per upload in this process: a concurrent retry shares the
        # running one instead of verifying and copying again. Across processes the
        # repository's `complete` decides: the same source answers the same ticket.
        flight = self._finalizing.get((org_id, upload_handle))
        if flight is None:
            flight = asyncio.ensure_future(self._finalize(org_id, upload_handle, key))
            self._finalizing[(org_id, upload_handle)] = flight
            flight.add_done_callback(
                lambda _: self._finalizing.pop((org_id, upload_handle), None))
        return await asyncio.shield(flight)

    async def _finalize(self, org_id: str, handle: str, key: str) -> MediaRef:
        try:
            ref = await self._resolved(org_id, handle)
        except errors.DomainError as refused:
            if refusal_of(refused) is not R.upload_not_finalized:
                raise                            # not_found, upload_expired, the object gone
        else:
            # Idempotent, and only for the bytes it was finalized with: a destination
            # overwritten since then is a conflict, never a second object.
            if await self.objects.head(key) not in (None, ref.digest):
                raise errors.Conflict(f"upload {handle} was finalized with other bytes")
            return ref
        ref = await self._verified(org_id, handle, key)
        ticket = await self.tickets.complete(org_id, handle, ref)
        if ref.storage_ref in self.idle_since:  # a use; first sight is the collector's own
            self.idle_since[ref.storage_ref] = self.now()
        return self._ref_of(ticket)

    async def _verified(self, org_id: str, handle: str, key: str) -> MediaRef:
        """The destination's bytes measured, checked against the ticket and written to the
        content-addressed source key: the ref `complete` is asked to commit. Under the
        preparation pool's bound, so at most that many downloads are in memory."""
        async with self.gate:
            described = await self.objects.describe(key)
            if described is None:
                # Nothing arrived yet: not a refusal, the client may still upload.
                raise errors.InvalidRequest("no object was uploaded to the issued destination")
            size, declared_mime = described
            if size > self.limits.max_media_bytes:       # refused before it is downloaded
                raise await self._abort(org_id, handle, R.too_large, errors.RequestTooLarge(
                    f"uploaded {size} bytes over MAX_MEDIA_BYTES"))
            data = await self.objects.get(key)
            if data is None:
                raise errors.InvalidRequest("no object was uploaded to the issued destination")
            digest = await asyncio.to_thread(digest_of, data)
            try:
                # What is at the destination, measured: the receipt a lost PUT acknowledgement
                # never recorded, or the same one again. Its ticket carries the constraints.
                ticket = await self.tickets.acknowledge_put(org_id, handle, bytes=len(data),
                                                            digest=digest)
            except errors.RequestTooLarge as refused:     # the bytes, not the HEAD, decide
                raise await self._abort(org_id, handle, R.too_large, refused)
            c = ticket.constraints
            if c.bytes is not None and len(data) != c.bytes:
                raise await self._abort(org_id, handle, R.size_mismatch, errors.InvalidRequest(
                    f"uploaded {len(data)} bytes, declared {c.bytes}"))
            if c.digest is not None and digest != c.digest:
                raise await self._abort(org_id, handle, R.digest_mismatch)
            try:
                # The container, not the client's Content-Type: M2's probe when preparation
                # is configured. A probe that timed out (platform-side) leaves it open.
                mime, duration_s = await self.facts(data, declared_mime)
            except errors.InvalidRequest as refused:
                raise await self._abort(org_id, handle, R.media_refused, refused)
            if mime not in c.accepted_mime:
                raise await self._abort(org_id, handle, R.mime_not_accepted,
                                        errors.UnsupportedMedia(f"{mime} is not accepted"))
            ref = MediaRef(org_id=org_id, handle=handle, kind=MediaKind.upload, digest=digest,
                           bytes=len(data), mime=mime,
                           storage_ref=self._key(org_id, digest, self.profile_version,
                                                 "source"),
                           profile_version=self.profile_version, duration_s=duration_s)
            # Before the commit: a finalized ticket always names an object that exists.
            await self._register(ContentKind.source, org_id, ref.storage_ref, digest,
                                 len(data))
            await self._write_once(ref.storage_ref, data, mime)
            return ref

    async def _abort(self, org_id: str, handle: str, reason: R,
                     error: errors.DomainError | None = None) -> errors.DomainError:
        """A failed check is final: the ticket is aborted durably before it is answered, so
        a second finalize - here or in another process - is not a second chance at it."""
        await self.tickets.abort(org_id, handle, reason)
        return error if error is not None else refuse(reason, f"the upload failed: {reason}")

    def _ref_of(self, ticket: UploadTicket) -> MediaRef:
        """The ref a finalized ticket names. The object key is rebuilt from the tenant, the
        digest and the profile: the repository stores no path, and no caller names one."""
        done = ticket.finalized
        return MediaRef(org_id=ticket.org_id, handle=ticket.upload_handle,
                        kind=MediaKind.upload, digest=done.digest, bytes=done.bytes,
                        mime=done.mime,
                        storage_ref=self._key(ticket.org_id, done.digest,
                                              done.profile_version, "source"),
                        profile_version=done.profile_version, duration_s=done.duration_s)

    async def _resolved(self, org_id: str, handle: str) -> MediaRef:
        """The repository's finalized ticket as a ref, its object re-checked: collected, or
        replaced behind the store, is `not_found` - a job must not run on either."""
        media = self._ref_of(await self.tickets.resolve(org_id, handle))
        if await self.objects.head(media.storage_ref) != media.digest:
            raise errors.NotFound(f"the object for upload {handle} is gone")
        return media

    # --- use ------------------------------------------------------------------
    async def materialize(self, org_id: str, source: str) -> MediaRef:
        """MPILOT gap 1: the third source form (R61(1)), which `validate.py` lets through.
        An `infrx-upload:upl_…` source is the caller's finalized upload, resolved by
        `resolve_owned` - the rule `stage` applies to an upload ref, so admission and
        staging can never disagree about one. Its ref is already content-addressed at the
        `source` key a fetched URL of the same bytes gets (finalize copied it there), and the
        request's byte budget (`_materialize_all`) bounds it like any source."""
        if not source.startswith(UPLOAD_REF_SCHEME):
            return await super().materialize(org_id, source)
        return await self.resolve_owned(valid_org(org_id), source[len(UPLOAD_REF_SCHEME):])

    async def resolve_owned(self, org_id: str, ref: str) -> MediaRef:
        """An upload handle resolves through the repository (R99): another org's or an
        unknown one `not_found`, past its window `upload_expired` (R99(b)), not finalized
        `not_found` (R99(a)), its object gone `not_found`. Any other handle is M1's index."""
        if not isinstance(ref, str) or not UPLOAD_HANDLE_RE.fullmatch(ref):
            return await super().resolve_owned(org_id, ref)
        try:
            return await self._resolved(org_id, ref)
        except errors.DomainError as refused:
            if refusal_of(refused) is R.upload_not_finalized:
                raise errors.NotFound(f"upload {ref} is not finalized") from None
            raise

    async def _staged_ref(self, org_id: str, ref: MediaRef) -> MediaRef | None:
        """M5 item 3: an admitted job's upload ref binds by the object it names, not by the
        ticket's window - `stage` resolved it through the ticket before admission, and the
        job outlives that window. It must be this tenant's content-addressed source object,
        still carrying that digest."""
        if ref.kind is not MediaKind.upload:
            return await super()._staged_ref(org_id, ref)
        if ref.storage_ref != self._key(org_id, ref.digest, ref.profile_version, "source") \
                or await self.objects.head(ref.storage_ref) != ref.digest:
            return None
        return ref

    async def stage(self, org_id, request):
        refs = await super().stage(org_id, request)
        self._touch(refs)
        return refs

    async def attach(self, job_id, refs) -> None:
        await super().attach(job_id, refs)
        self._touch(self.by_job[job_id])
