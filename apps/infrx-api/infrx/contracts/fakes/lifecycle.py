"""The reference adapter of `contracts.v2.lifecycle`'s three ports, in memory.

`FakeLifecycle` is one store the way D10's PostgreSQL is one database: tickets, content
rows, references and readiness markers live in a shared `Durable` record, and
`reopen()` is another process over the same state (nothing is kept per instance, which is
exactly what `MediaUploads.uploads` and `MediaCollector` got wrong - RV-02/RV-03). One
`asyncio.Lock` over `Durable` stands for the row locks that serialize admission,
completion and deletion.

Admission itself is the existing `FakeJobStore` (`jobs`): `admit_ready` runs every
lifecycle check first, then `admit`/`admit_credit`, then records the marker, all inside
the lock with no await that yields to another lifecycle operation, so it is one
transaction as far as any case can observe.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .. import errors, ids
from ..limits import DEFAULTS
from ..records import ExecutionMode, MediaKind, MediaRef, UploadState
from ..v2.lifecycle import (MAX_PAGE, AdmissionExpectation, ContentIdentity, ContentKind,
                            ContentLocation, ContentObject, ContentOrigin, ContentPage,
                            ContentReference, DeletionClaim, ExecutionReadiness,
                            FinalizedSource, LifecycleRefusal as R, LifecycleState,
                            ManifestSource, Tombstone, UploadConstraints, UploadReceipt,
                            UploadTicket, DESTINATION_SCHEME, UPLOAD_ABORT_REASONS, refuse)
from ..v2.records import AccountingRegime, AdmissionV2

UPLOAD_TTL_S = DEFAULTS.processing_cache_ttl_s        # the window MediaUploads uses today
GRACE_S = DEFAULTS.processing_cache_ttl_s             # MediaCollector's grace today
CLAIM_TTL_S = 60.0
RETENTION_S = DEFAULTS.processing_cache_ttl_s         # P-25 pending: a fixture value only
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass
class Durable:
    """Everything a restart must not lose."""

    tickets: dict[str, UploadTicket] = field(default_factory=dict)
    content: dict[str, ContentObject] = field(default_factory=dict)
    by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    references: dict[str, list[ContentReference]] = field(default_factory=dict)
    readiness: dict[str, ExecutionReadiness] = field(default_factory=dict)
    retain: dict[tuple, datetime] = field(default_factory=dict)   # (job, kind) -> until
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class FakeLifecycle:
    def __init__(self, jobs, clock, ids_, *, durable: Durable | None = None,
                 upload_ttl_s: float = UPLOAD_TTL_S, grace_s: float = GRACE_S,
                 claim_ttl_s: float = CLAIM_TTL_S, retention_s: float = RETENTION_S) -> None:
        self.jobs, self.clock, self.ids = jobs, clock, ids_
        self.d = durable or Durable()
        self.upload_ttl_s, self.grace_s = upload_ttl_s, grace_s
        self.claim_ttl_s, self.retention_s = claim_ttl_s, retention_s

    def reopen(self) -> FakeLifecycle:
        """Another process over the same durable state."""
        return FakeLifecycle(self.jobs, self.clock, self.ids, durable=self.d,
                             upload_ttl_s=self.upload_ttl_s, grace_s=self.grace_s,
                             claim_ttl_s=self.claim_ttl_s, retention_s=self.retention_s)

    def _put(self, ticket: UploadTicket, **changes) -> UploadTicket:
        # Revalidated, never `model_copy(update=)` (R78): the record's own rules hold.
        ticket = UploadTicket.model_validate({**ticket.model_dump(), **changes})
        self.d.tickets[ticket.upload_handle] = ticket
        return ticket

    def _owned(self, org_id: str, handle: object) -> UploadTicket:
        ticket = self.d.tickets.get(handle) if isinstance(handle, str) else None
        if ticket is None or ticket.org_id != org_id:
            raise refuse(R.not_found, f"no upload {handle!r} for this organization")
        return ticket

    def _open(self, ticket: UploadTicket) -> None:
        if ticket.state is not UploadState.created:
            raise refuse(R.upload_not_open, f"upload is {ticket.state}")
        if self.clock.now() >= ticket.expires_at:
            raise refuse(R.upload_expired, "the upload window closed")

    # --- UploadRepository ----------------------------------------------------------------
    async def create(self, org_id: str, constraints: UploadConstraints) -> UploadTicket:
        if not ids.is_request_id(org_id):
            raise errors.InvalidRequest("org_id must be a lowercase UUID")
        if not isinstance(constraints, UploadConstraints):
            raise refuse(R.invalid_constraints, "constraints are an UploadConstraints record")
        async with self.d.lock:
            now, handle = self.clock.now(), self.ids.upload_handle()
            return self._put(UploadTicket(
                upload_handle=handle, org_id=org_id, destination_ref=DESTINATION_SCHEME + handle,
                constraints=constraints, state=UploadState.created, created_at=now,
                expires_at=now + timedelta(seconds=self.upload_ttl_s)))

    async def acknowledge_put(self, org_id: str, upload_handle: str, *, bytes: int,
                              digest: str) -> UploadTicket:
        async with self.d.lock:
            ticket = self._owned(org_id, upload_handle)
            self._open(ticket)
            if isinstance(bytes, bool) or not isinstance(bytes, int) or bytes < 0:
                raise errors.InvalidRequest("bytes is a nonnegative integer")
            if bytes > ticket.constraints.max_bytes:
                raise refuse(R.too_large, "over the ticket's max_bytes")
            if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise errors.InvalidRequest("digest is sha256:<64 hex>")
            receipt = UploadReceipt(bytes=bytes, digest=digest, received_at=self.clock.now())
            if ticket.received is not None:
                if (ticket.received.bytes, ticket.received.digest) != (bytes, digest):
                    raise refuse(R.bytes_changed, "the handle already received other bytes")
                return ticket
            return self._put(ticket, received=receipt)

    async def complete(self, org_id: str, upload_handle: str, source: MediaRef) -> UploadTicket:
        async with self.d.lock:
            ticket = self._owned(org_id, upload_handle)
            if (source.org_id, source.handle, source.kind) != (org_id, upload_handle,
                                                               MediaKind.upload):
                raise refuse(R.not_found, "the source is not this ticket's")
            done = ticket.finalized
            if done is not None:
                row = self.d.content.get(done.content_id)
                if (done.digest, done.bytes, done.mime) != (source.digest, source.bytes,
                                                           source.mime) \
                        or row is None or row.identity.object_key != source.storage_ref:
                    raise refuse(R.bytes_changed, "finalized with other bytes")
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
                self._put(ticket, state=UploadState.aborted, refusal=failed)
                raise refuse(failed, "a constraint failed; the ticket is aborted")
            row = self._register(ContentIdentity(
                org_id=org_id, kind=ContentKind.source, location=ContentLocation.object_store,
                object_key=source.storage_ref, digest=source.digest, bytes=source.bytes,
                origin=ContentOrigin.written))
            return self._put(ticket, state=UploadState.finalized, finalized=FinalizedSource(
                content_id=row.content_id, generation=row.generation, digest=source.digest,
                bytes=source.bytes, mime=source.mime, profile_version=source.profile_version,
                duration_s=source.duration_s, finalized_at=self.clock.now()))

    async def abort(self, org_id: str, upload_handle: str, refusal: R) -> UploadTicket:
        if refusal not in UPLOAD_ABORT_REASONS:
            raise errors.InvalidRequest("a ticket records only a public upload refusal")
        async with self.d.lock:
            ticket = self._owned(org_id, upload_handle)
            if ticket.state is UploadState.aborted:
                return ticket
            if ticket.state is not UploadState.created or self.clock.now() >= ticket.expires_at:
                raise refuse(R.upload_not_open, f"upload is {ticket.state} or past its window")
            return self._put(ticket, state=UploadState.aborted, refusal=R(refusal))

    async def resolve(self, org_id: str, upload_handle: str) -> UploadTicket:
        async with self.d.lock:
            return self._resolve(org_id, upload_handle)

    def _resolve(self, org_id: str, upload_handle: str) -> UploadTicket:
        ticket = self._owned(org_id, upload_handle)
        if self.clock.now() >= ticket.expires_at:
            raise refuse(R.upload_expired, "the upload window has expired")
        if ticket.state is not UploadState.finalized:
            raise refuse(R.upload_not_finalized, f"upload is {ticket.state}")
        row = self.d.content.get(ticket.finalized.content_id)
        if row is None or row.state is not LifecycleState.live \
                or row.generation != ticket.finalized.generation:
            raise refuse(R.not_found, "the finalized source is gone")
        return ticket

    async def expire(self, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise errors.InvalidRequest("limit is a positive integer")
        async with self.d.lock:
            now = self.clock.now()
            due = sorted((t for t in self.d.tickets.values()
                          if t.state is UploadState.created and now >= t.expires_at),
                         key=lambda t: (t.expires_at, t.upload_handle))[:limit]
            for ticket in due:
                self._put(ticket, state=UploadState.expired)
            return len(due)

    # --- ReadinessStore ------------------------------------------------------------------
    async def admit_ready(self, request, idem, expectation: AdmissionExpectation):
        if not isinstance(expectation, AdmissionExpectation):
            raise errors.InvalidRequest("an AdmissionExpectation from the runtime is required")
        credit = expectation.accounting_regime is AccountingRegime.credit
        async with self.d.lock:
            found = await self.jobs.lookup(request.org_id, idem) if idem.key else None
            if found is not None:
                return self._replayed(found[0], credit)
            sources = self._manifest(request)
            if credit:
                pins, _card, _policy, _wallet = await self.jobs._credit_terms(request)
                if pins.rate_card_version != expectation.rate_card_version:
                    raise refuse(R.expectation_mismatch, "the pinned card is not this runtime's")
                serving = await self.jobs.catalog.serving_revision(pins.serving_version_id)
                if serving is None:
                    raise errors.NotFound("the pinned serving revision is not in the catalog")
                _check_pinned_capability(serving.capability, request)
                admission = await self.jobs.admit_credit(request, idem)
            else:
                serving = self._legacy_serving(request.model_revision)
                if serving is not None:             # a pre-catalog model: nothing to check
                    _check_pinned_capability(serving.capability, request)
                admission = await self.jobs.admit(request, idem, ())
            if admission.replayed:
                return self._replayed(admission, credit)
            now = self.clock.now()
            readiness = ExecutionReadiness(job_id=admission.request_id, org_id=admission.org_id,
                                           sources=sources, ready_at=admission.admitted_at)
            self.d.readiness[admission.request_id] = readiness
            for source in sources:
                self.d.references.setdefault(source.content_id, []).append(ContentReference(
                    content_id=source.content_id, generation=source.generation,
                    job_id=admission.request_id, org_id=admission.org_id, referenced_at=now))
            return admission, readiness

    def _legacy_serving(self, model_revision: str):
        """0019: a legacy job's `<alias>@<label>` names the serving revision of that label,
        if the catalog has one. ponytail: a scan of the v2 fake catalog's rows; a catalog
        without `servings` models no revision, so nothing is checked (a pre-catalog model)."""
        servings = getattr(getattr(self.jobs, "catalog", None), "servings", {})
        return next((serving for serving in servings.values()
                     if f"{serving.public_model_id}@{serving.revision_label}" == model_revision),
                    None)

    def _replayed(self, admission, credit: bool):
        if isinstance(admission, AdmissionV2) is not credit:
            raise errors.IdempotencyConflict("the key names a job of another regime")
        return admission, self.d.readiness.get(admission.request_id)

    def _manifest(self, request) -> tuple[ManifestSource, ...]:
        """Each ref -> a live source row of the request's organization, same digest."""
        if len({ref.handle for ref in request.media}) != len(request.media):
            raise refuse(R.invalid_manifest, "a manifest names each source once")
        sources = []
        for ref in request.media:
            row = self.d.content.get(self.d.by_key.get(
                (ContentLocation.object_store.value, ref.storage_ref), ""))
            if row is None or row.identity.org_id != request.org_id \
                    or row.identity.kind is not ContentKind.source \
                    or row.identity.digest != ref.digest or ref.org_id != request.org_id \
                    or row.state is LifecycleState.deleted:
                raise refuse(R.not_found, f"source {ref.handle} is not this org's content")
            if row.state is LifecycleState.tombstoned:
                raise refuse(R.content_retiring, f"source {ref.handle} is being deleted")
            if ref.kind is MediaKind.upload:
                ticket = self._resolve(request.org_id, ref.handle)
                if ticket.finalized.content_id != row.content_id:
                    raise refuse(R.not_found, "the upload names other content")
            sources.append(ManifestSource(content_id=row.content_id, generation=row.generation,
                                          ref=ref))
        return tuple(sources)

    async def readiness(self, job_id: str) -> ExecutionReadiness | None:
        return self.d.readiness.get(job_id) if ids.is_request_id(job_id) else None

    async def claim_preparation(self, job_id: str, worker_id: str):
        if job_id not in self.d.readiness:
            raise refuse(R.not_ready, f"job {job_id} has no execution-ready marker")
        return await self.jobs.claim_preparation(job_id, worker_id)

    # --- ContentLifecycle ----------------------------------------------------------------
    def _job_live(self, job_id: str, now: datetime,
                  kind: ContentKind = ContentKind.source) -> bool:
        """Non-terminal, or terminal and before its retain_until - set once, here, from the
        job's persisted facts (never recomputed): a result lasts exactly until the outcome's
        persisted `result_expires_at` (a job without one keeps no result), anything else
        the configured serving retention after settlement."""
        job = self.jobs.jobs.get(job_id)
        if job is None:
            return False
        if job.outcome is None:
            return True
        outcome = job.outcome
        until = self.d.retain.setdefault((job_id, kind), (
            outcome.result_expires_at or outcome.settled_at if kind is ContentKind.result
            else outcome.settled_at + timedelta(seconds=self.retention_s)))
        return now < until

    def _referenced(self, row: ContentObject, now: datetime) -> bool:
        identity = row.identity
        if any(ref.generation == row.generation and self._job_live(ref.job_id, now)
               for ref in self.d.references.get(row.content_id, ())):
            return True
        if identity.job_id is not None and self._job_live(identity.job_id, now, identity.kind):
            return True
        if identity.kind is ContentKind.upload_destination:
            ticket = self.d.tickets.get(identity.upload_handle)
            return ticket is not None and ticket.org_id == identity.org_id \
                and ticket.state is UploadState.created and now < ticket.expires_at
        return any(t.finalized is not None and t.finalized.content_id == row.content_id
                   and t.finalized.generation == row.generation and now < t.expires_at
                   for t in self.d.tickets.values())

    def _save(self, row: ContentObject, **changes) -> ContentObject:
        row = ContentObject.model_validate({**row.model_dump(), **changes})
        self.d.content[row.content_id] = row
        return row

    def _register(self, identity: ContentIdentity) -> ContentObject:
        key = (identity.location.value, identity.object_key)
        now = self.clock.now()
        fresh = dict(state=LifecycleState.live, registered_at=now,
                     eligible_at=now + timedelta(seconds=self.grace_s), claim=None,
                     tombstoned_at=None, deleted_at=None)
        row = self.d.content.get(self.d.by_key.get(key, ""))
        if row is None:
            row = ContentObject(content_id=self.ids.uuid(), generation=1, identity=identity,
                                **fresh)
            self.d.by_key[key] = row.content_id
            return self._save(row)
        if row.identity.org_id != identity.org_id:
            raise refuse(R.not_found, "the key is not this organization's")
        if row.state is LifecycleState.tombstoned:
            raise refuse(R.content_retiring, "a delete of this key is not yet acknowledged")
        if row.state is LifecycleState.deleted:
            return self._save(row, generation=row.generation + 1, identity=identity, **fresh)
        if None not in (row.identity.digest, identity.digest) \
                and row.identity.digest != identity.digest:
            raise refuse(R.bytes_changed, "the key already names other bytes")
        if identity.origin is ContentOrigin.written and row.state is LifecycleState.live \
                and row.identity.digest is not None and row.identity.digest == identity.digest:
            # M6 WR-7 (D10 0022): the runtime rewrites these bytes for a new request, so the
            # grace restarts (never shortens); a `discovered` registration never refreshes.
            return self._save(row, eligible_at=max(row.eligible_at,
                                                   now + timedelta(seconds=self.grace_s)))
        return row

    async def register(self, identity: ContentIdentity) -> ContentObject:
        async with self.d.lock:
            return self._register(identity)

    async def references(self, content_id: str) -> tuple[ContentReference, ...]:
        async with self.d.lock:
            if content_id not in self.d.content:
                raise refuse(R.not_found, "no such content")
            refs = self.d.references.get(content_id, ())
            for ref in refs:
                self._job_live(ref.job_id, self.clock.now())    # persists retain_until once
            return tuple(ContentReference.model_validate(
                {**ref.model_dump(), "retain_until": self.d.retain.get(
                    (ref.job_id, ContentKind.source))})
                for ref in refs)

    def _claimable(self, row: ContentObject, now: datetime) -> bool:
        claim_free = row.claim is None or now >= row.claim.expires_at
        if row.state is LifecycleState.tombstoned:
            return claim_free
        return row.state is LifecycleState.live and claim_free and now >= row.eligible_at \
            and not self._referenced(row, now)

    async def candidates(self, *, after: str | None, limit: int) -> ContentPage:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise errors.InvalidRequest("limit is a positive integer")
        limit = min(limit, MAX_PAGE)
        start = _decode(after) if after is not None else None
        async with self.d.lock:
            now = self.clock.now()
            rows = sorted((r for r in self.d.content.values() if self._claimable(r, now)),
                          key=lambda r: (r.eligible_at, r.content_id))
            if start is not None:
                rows = [r for r in rows if (r.eligible_at, r.content_id) > start]
            page = tuple(rows[:limit])
            more = len(rows) > limit
            return ContentPage(items=page, next_cursor=_encode(page[-1]) if more else None)

    async def claim(self, content_id: str, generation: int, holder: str) -> DeletionClaim:
        async with self.d.lock:
            row = self.d.content.get(content_id)
            if row is None:
                raise refuse(R.not_found, "no such content")
            now = self.clock.now()
            if row.generation != generation or row.state is LifecycleState.deleted:
                raise refuse(R.claim_lost, "another generation of this object")
            if row.claim is not None and now < row.claim.expires_at:
                raise refuse(R.claim_held, "an unexpired claim holds this object")
            if row.state is LifecycleState.live:
                self._recheck(row, now)
            claim = DeletionClaim(content_id=content_id, generation=generation,
                                  fence=(row.claim.fence if row.claim else 0) + 1, holder=holder,
                                  claimed_at=now,
                                  expires_at=now + timedelta(seconds=self.claim_ttl_s))
            self._save(row, claim=claim)
            return claim

    def _recheck(self, row: ContentObject, now: datetime) -> None:
        if now < row.eligible_at:
            raise refuse(R.not_eligible, "inside the persisted grace")
        if self._referenced(row, now):
            raise refuse(R.reference_live, "a live reference protects this object")

    async def tombstone(self, claim: DeletionClaim) -> Tombstone:
        async with self.d.lock:
            row = self.d.content.get(claim.content_id)
            if row is None:
                raise refuse(R.not_found, "no such content")
            now = self.clock.now()
            if row.generation != claim.generation or row.claim is None \
                    or row.claim.fence != claim.fence or now >= row.claim.expires_at:
                raise refuse(R.claim_lost, "the claim is not the current, unexpired fence")
            if row.state is LifecycleState.live:
                self._recheck(row, now)
                row = self._save(row, state=LifecycleState.tombstoned, tombstoned_at=now)
            return Tombstone(content_id=row.content_id, generation=row.generation,
                             fence=claim.fence, location=row.identity.location,
                             object_key=row.identity.object_key, tombstoned_at=row.tombstoned_at)

    async def acknowledge_delete(self, tombstone: Tombstone) -> ContentObject:
        async with self.d.lock:
            row = self.d.content.get(tombstone.content_id)
            if row is None:
                raise refuse(R.not_found, "no such content")
            if row.generation != tombstone.generation or row.state is LifecycleState.deleted:
                return row          # an older generation's delete, or a repeat: no change
            if row.state is not LifecycleState.tombstoned or row.claim is None \
                    or row.claim.fence != tombstone.fence:
                raise refuse(R.claim_lost, "a newer claim finishes this delete")
            return self._save(row, state=LifecycleState.deleted, deleted_at=self.clock.now())


def _encode(row: ContentObject) -> str:
    raw = json.dumps([row.eligible_at.isoformat(), row.content_id]).encode()
    return "cc1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: object) -> tuple[datetime, str]:
    try:
        if not isinstance(cursor, str) or not cursor.startswith("cc1."):
            raise ValueError(cursor)
        body = cursor[4:] + "=" * (-len(cursor[4:]) % 4)
        at, content_id = json.loads(base64.urlsafe_b64decode(body))
        return datetime.fromisoformat(at), ids.require_request_id(content_id)
    except (ValueError, TypeError):
        raise errors.InvalidCursor("not a content cursor this store issued") from None


def _check_pinned_capability(capability, request) -> None:
    """0019 `check_pinned_capability`, part for part: a string content is text, a
    `video_url` part video, any other part its own type, and media present needs video - all
    within the PINNED revision's `input_modalities` (`unsupported_media`, `param=messages`);
    a stream needs `stream_output` (`unsupported_parameter`, `param=stream`)."""
    needed = {"video"} if request.media else set()
    for message in request.messages:
        content = message.get("content")
        if isinstance(content, str):
            needed.add("text")
        elif isinstance(content, (list, tuple)):
            needed.update("video" if part.get("type") == "video_url" else part.get("type")
                          for part in content if isinstance(part, dict) and part.get("type"))
    if not needed <= set(capability.input_modalities):
        raise errors.UnsupportedMedia("the model does not accept this input modality",
                                      param="messages")
    if request.execution_mode is ExecutionMode.stream and not capability.stream_output:
        raise errors.UnsupportedParameter("the model does not stream its output",
                                          param="stream")
