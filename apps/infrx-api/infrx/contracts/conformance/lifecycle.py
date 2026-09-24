"""The exported F2C.a lifecycle cases: uploads, execution readiness, content cleanup.

    from infrx.contracts.conformance.lifecycle import run_lifecycle_conformance
    run_lifecycle_conformance(my_factory)            # raises AssertionError on failure

One oracle per test id of `04-verification.md`: `upload_restart__`, `admission_ready__`,
`retention_durable__`, `result_expiry__` (F2C.b). `factory() -> Harness` returns a FRESH store whose `port`
implements `UploadRepository`, `ReadinessStore` and `ContentLifecycle`, plus hooks:

* `reopen()` - the same durable state as another process sees it (a new adapter, a new
  pool: nothing carried over in memory);
* `jobs` - the JobStore (CREDIT regime) the store admits through; its `admit_credit` is
  the PREVIOUS runtime's admission, which writes no marker;
* `credit_balance(wallet_id)` - `{"ledger", "reserved", "available"}`;
* `set_capability(serving_version_id, input_modalities)` - the catalog row a later alias
  move would change;
* `retune(**limits)` - the store's CURRENT configuration (`result_ttl_s`), as an operator
  changing it would.

The store is seeded with the v2 fixture directories (consumer credential, wallet, the
Marlin revision and card). Every instant a case waits for is read off a returned record
(`expires_at`, `eligible_at`, a claim's `expires_at`, a reference's `retain_until`), never
from a constant, so a real adapter may configure any window with grace < upload window.
Green against the fake means implemented; only green against D10's PostgreSQL adapter
means integrated.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Callable

from .. import errors
from ..records import (ExecutionMode, IdempotencyRef, LeaseKind, MediaKind, MediaRef,
                       TerminalOutcome, UploadState)
from ..v2 import fixtures as v2fix
from ..v2.lifecycle import (MAX_PAGE, REFUSAL_ERRORS, AdmissionExpectation, ContentIdentity, ContentKind, ContentLocation,
                            ContentOrigin, LifecycleRefusal as R, LifecycleState, ReadinessState,
                            ReadOutcome, Tombstone, UploadConstraints, read_outcome,
                            readiness_view, refusal_of)
from ..v2.records import AccountingRegime
from . import builders as b
from .harness import hook

IDS = v2fix.IDS
ORG, OTHER = IDS.consumer_org, IDS.other_org
MP4 = frozenset({"video/mp4", "video/webm"})
CARD = AdmissionExpectation(accounting_regime=AccountingRegime.credit,
                            rate_card_version=v2fix.RATE_CARD_VERSION)
LEGACY = AdmissionExpectation(accounting_regime=AccountingRegime.legacy_usd)
# Both regimes admit through `admit_ready`; a case about admission runs for each, so an
# adapter that forgets the legacy marker cannot pass (W5 would refuse every legacy job).
EXPECTATIONS = (CARD, LEGACY)


# --- helpers (records only; nothing here reads a fake's attributes) -----------------------
def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ref(data: bytes, *, org: str = ORG, handle: str | None = None,
         kind: MediaKind = MediaKind.url, mime: str = "video/mp4") -> MediaRef:
    digest = _digest(data)
    return MediaRef(org_id=org, handle=handle or "med_" + digest[7:47], kind=kind, digest=digest,
                    bytes=len(data), mime=mime, duration_s=4.0,
                    storage_ref=f"media/{org}/v1/{digest[7:23]}/source")


def _written(ref: MediaRef) -> ContentIdentity:
    return ContentIdentity(org_id=ref.org_id, kind=ContentKind.source,
                           location=ContentLocation.object_store, object_key=ref.storage_ref,
                           digest=ref.digest, bytes=ref.bytes, origin=ContentOrigin.written)


async def _staged(harness, data: bytes, **kw) -> MediaRef:
    """What M does before admission: register, then (not modelled) write the object."""
    ref = _ref(data, **kw)
    await harness.port.register(_written(ref))
    return ref


async def _uploaded(harness, data: bytes, **constraints):
    """create -> acknowledge_put -> complete; the ticket and the ref M would stage."""
    port = harness.port
    ticket = await port.create(ORG, UploadConstraints.parse(
        constraints, max_media_bytes=1 << 26, allowed_mime=MP4))
    await port.acknowledge_put(ORG, ticket.upload_handle, bytes=len(data), digest=_digest(data))
    ref = _ref(data, handle=ticket.upload_handle, kind=MediaKind.upload)
    return await port.complete(ORG, ticket.upload_handle, ref), ref


def _request(harness, refs=(), mode=ExecutionMode.async_):
    return b.request(harness, org_id=ORG, key_id=IDS.consumer_key,
                     model_revision=v2fix.REQUESTED_MODEL, refs=tuple(refs), mode=mode)


def _idem(request, key: str = "ready-1") -> IdempotencyRef:
    return b.idem(request, key)


async def _refused(call, reason: R) -> errors.DomainError:
    try:
        await call
    except errors.DomainError as error:
        assert refusal_of(error) is reason, f"expected {reason}, got {refusal_of(error)} ({error})"
        assert error.code == REFUSAL_ERRORS[reason].code, (reason, error.code)
        return error
    raise AssertionError(f"expected the refusal {reason}")


def _balances(harness):
    """Available CREDIT and available USD: a refusal or a scrub moves neither."""
    return (hook(harness, "credit_balance")(IDS.consumer_wallet)["available"],
            hook(harness, "balance")(ORG)["available"])


async def _nothing_admitted(harness, request, idem, before) -> None:
    jobs = hook(harness, "jobs")
    assert await jobs.lookup(request.org_id, idem) is None, "a refused admission left a job"
    assert _balances(harness) == before, "a refused admission moved money"


def _previous_admit(harness, expectation):
    """The PREVIOUS runtime's admission for this regime: it writes no marker."""
    jobs = hook(harness, "jobs")
    if expectation.accounting_regime is AccountingRegime.credit:
        return jobs.admit_credit
    return lambda request, idem: jobs.admit(request, idem, ())


def _key(name: str, expectation) -> str:
    return f"{name}-{expectation.accounting_regime.value}"


def _at(harness, instant: datetime) -> None:
    """Move the store clock to exactly `instant` (the equality boundary)."""
    harness.clock.advance((instant - harness.clock.now()).total_seconds())


# --- UPLOAD-RESTART ------------------------------------------------------------------------
async def upload_restart__every_step_survives_a_new_process(factory):
    """RV-02: create, put, complete, resolve and a completion retry each run in a NEW
    process over the same durable store, and every one sees the same ticket. The old
    `MediaUploads` kept tickets in `self.uploads` and failed at the first reopen."""
    harness = factory()
    reopen = hook(harness, "reopen")
    data = b"clip-restart"
    ticket = await harness.port.create(ORG, UploadConstraints.parse(
        {"bytes": len(data), "digest": _digest(data)}, max_media_bytes=1 << 26,
        allowed_mime=MP4))
    handle = ticket.upload_handle
    received = await reopen().acknowledge_put(ORG, handle, bytes=len(data), digest=_digest(data))
    assert received.received is not None and received.state is UploadState.created
    ref = _ref(data, handle=handle, kind=MediaKind.upload)
    done = await reopen().complete(ORG, handle, ref)
    assert done.state is UploadState.finalized and done.finalized.digest == _digest(data)
    assert await reopen().resolve(ORG, handle) == done
    assert await reopen().complete(ORG, handle, ref) == done, "a completion retry changed it"
    assert (done.created_at, done.expires_at, done.constraints) == (
        ticket.created_at, ticket.expires_at, ticket.constraints), "the promise moved"


async def upload_restart__the_tenant_handle_and_window_are_the_stores(factory):
    """No caller-selected tenant, handle or expiry: a create body naming one is refused,
    the window is the store's clock, and another tenant's handle is the same `not_found`
    as an unknown one on every operation."""
    harness = factory()
    port = harness.port
    for smuggled in ({"org_id": OTHER}, {"expires_at": "2030-01-01T00:00:00Z"},
                     {"upload_handle": "upl_" + "x" * 30}, {"destination_ref": "s3://b/k"},
                     {"max_bytes": "4096"}, {"max_bytes": True}, {"accepted_mime": "video/mp4"},
                     {"max_bytes": (1 << 26) + 1}, {"accepted_mime": ["image/png"]},
                     {"schema_version": 2}):
        try:
            UploadConstraints.parse(smuggled, max_media_bytes=1 << 26, allowed_mime=MP4)
        except errors.InvalidRequest as refused:
            assert refusal_of(refused) is R.invalid_constraints, smuggled
        else:
            raise AssertionError(f"constraints accepted {smuggled}")
    ticket = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                            allowed_mime=MP4))
    assert ticket.org_id == ORG and ticket.created_at == harness.clock.now()
    assert ticket.expires_at > ticket.created_at
    handle, unknown = ticket.upload_handle, "upl_" + "u" * 40
    data = b"foreign"
    for org, name in ((OTHER, handle), (ORG, unknown)):
        await _refused(port.acknowledge_put(org, name, bytes=1, digest=_digest(data)),
                       R.not_found)
        await _refused(port.complete(org, name, _ref(data, org=org, handle=name,
                                                      kind=MediaKind.upload)), R.not_found)
        await _refused(port.resolve(org, name), R.not_found)
        await _refused(port.abort(org, name, R.digest_mismatch), R.not_found)
    assert (await port.acknowledge_put(ORG, handle, bytes=len(data), digest=_digest(data))
            ).state is UploadState.created, "the owner's ticket was disturbed"


async def upload_restart__one_handle_names_one_set_of_bytes(factory):
    """Same bytes again are idempotent; other bytes, a digest unlike the receipt, a
    relabelled or foreign source, and a second completion with other content conflict."""
    harness = factory()
    port = harness.port
    data, other = b"one-set", b"another-set"
    ticket = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                            allowed_mime=MP4))
    handle = ticket.upload_handle
    first = await port.acknowledge_put(ORG, handle, bytes=len(data), digest=_digest(data))
    harness.clock.advance(1)            # a retry later must not rewrite when they arrived
    assert await port.acknowledge_put(ORG, handle, bytes=len(data), digest=_digest(data)) == first
    await _refused(port.acknowledge_put(ORG, handle, bytes=len(other), digest=_digest(other)),
                   R.bytes_changed)
    await _refused(port.acknowledge_put(ORG, handle, bytes=len(data) + 1, digest=_digest(data)),
                   R.bytes_changed)
    try:
        await port.acknowledge_put(ORG, handle, bytes=len(data), digest="md5:0")
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("a malformed digest was received")
    forged = _ref(other, handle=handle, kind=MediaKind.upload)
    await _refused(port.complete(ORG, handle, forged), R.bytes_changed)
    for foreign in (_ref(data, org=OTHER, handle=handle, kind=MediaKind.upload),
                    _ref(data, handle="upl_" + "r" * 40, kind=MediaKind.upload),
                    _ref(data, handle=handle, kind=MediaKind.url)):
        await _refused(port.complete(ORG, handle, foreign), R.not_found)
    ref = _ref(data, handle=handle, kind=MediaKind.upload)
    done = await port.complete(ORG, handle, ref)
    assert await port.complete(ORG, handle, ref) == done
    await _refused(port.complete(ORG, handle, forged), R.bytes_changed)
    for changed in ({"mime": "video/webm"}, {"bytes": len(data) + 1}):
        await _refused(port.complete(ORG, handle, MediaRef.model_validate(
            {**ref.model_dump(), **changed})), R.bytes_changed)
    await _refused(port.acknowledge_put(ORG, handle, bytes=len(data), digest=_digest(data)),
                   R.upload_not_open)


async def upload_restart__the_window_bounds_completion_and_use(factory):
    """R22/R99(b) on the store clock: at `expires_at` exactly a put or a completion is
    `upload_expired`, `expire` closes open tickets once, and a finalized upload is
    unusable from its `expires_at` on - while it stays finalized."""
    harness = factory()
    port = harness.port
    data = b"window"
    open_ticket = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                                 allowed_mime=MP4))
    second = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                            allowed_mime=MP4))
    done, ref = await _uploaded(harness, data)
    assert await port.resolve(ORG, done.upload_handle) == done
    _at(harness, open_ticket.expires_at)
    await _refused(port.acknowledge_put(ORG, open_ticket.upload_handle, bytes=1,
                                        digest=_digest(b"x")), R.upload_expired)
    await _refused(port.complete(ORG, open_ticket.upload_handle,
                                 _ref(data, handle=open_ticket.upload_handle,
                                      kind=MediaKind.upload)), R.upload_expired)
    _at(harness, done.expires_at)
    await _refused(port.resolve(ORG, done.upload_handle), R.upload_expired)
    assert await port.expire(1) == 1, "expire exceeded its limit"
    assert await port.expire(100) == 1
    assert await port.expire(100) == 0, "expire is not idempotent"
    await _refused(port.resolve(ORG, second.upload_handle), R.upload_expired)
    await _refused(port.resolve(ORG, open_ticket.upload_handle), R.upload_expired)
    await _refused(port.abort(ORG, open_ticket.upload_handle, R.too_large), R.upload_not_open)
    assert (await port.complete(ORG, done.upload_handle, ref)).state is UploadState.finalized


async def upload_restart__oversize_puts_and_aborts_are_final_everywhere(factory):
    """A put over the ticket's cap is `too_large`; M's own abort is persisted (another
    process sees it), idempotent, limited to the public upload reasons, and refused on a
    finalized ticket."""
    harness = factory()
    port, reopen = harness.port, hook(harness, "reopen")
    data = b"x" * 64
    small = await port.create(ORG, UploadConstraints.parse(
        {"max_bytes": 16}, max_media_bytes=1 << 26, allowed_mime=MP4))
    await _refused(port.acknowledge_put(ORG, small.upload_handle, bytes=len(data),
                                        digest=_digest(data)), R.too_large)
    aborted = await port.abort(ORG, small.upload_handle, R.media_refused)
    assert (aborted.state, aborted.refusal) == (UploadState.aborted, R.media_refused)
    await _refused(reopen().resolve(ORG, small.upload_handle), R.upload_not_finalized)
    assert await reopen().abort(ORG, small.upload_handle, R.media_refused) == aborted
    await _refused(port.complete(ORG, small.upload_handle,
                                 _ref(data, handle=small.upload_handle, kind=MediaKind.upload)),
                   R.upload_not_open)
    other = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                           allowed_mime=MP4))
    for internal in (R.claim_lost, R.not_ready, R.not_found):
        try:
            await port.abort(ORG, other.upload_handle, internal)
        except errors.InvalidRequest:
            continue
        raise AssertionError(f"a ticket recorded the internal refusal {internal}")
    done, _ref_ = await _uploaded(harness, b"finalized-then-aborted")
    await _refused(port.abort(ORG, done.upload_handle, R.too_large), R.upload_not_open)


async def upload_restart__a_failed_check_is_final(factory):
    """A declared digest or type the bytes fail aborts the ticket in the completing
    transaction; nothing completes it afterwards, and it never resolves."""
    harness = factory()
    port = harness.port
    data = b"declared-wrong"
    for constraints, source_mime, reason in (
            ({"digest": _digest(b"something else")}, "video/mp4", R.digest_mismatch),
            ({"bytes": len(data) + 1}, "video/mp4", R.size_mismatch),
            ({"accepted_mime": ["video/webm"]}, "video/mp4", R.mime_not_accepted)):
        ticket = await port.create(ORG, UploadConstraints.parse(
            constraints, max_media_bytes=1 << 26, allowed_mime=MP4))
        handle = ticket.upload_handle
        await _refused(port.resolve(ORG, handle), R.upload_not_finalized)
        await port.acknowledge_put(ORG, handle, bytes=len(data), digest=_digest(data))
        ref = _ref(data, handle=handle, kind=MediaKind.upload, mime=source_mime)
        await _refused(port.complete(ORG, handle, ref), reason)
        await _refused(port.complete(ORG, handle, ref), R.upload_not_open)
        await _refused(port.resolve(ORG, handle), R.upload_not_finalized)
    nothing = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                             allowed_mime=MP4))
    await _refused(port.complete(ORG, nothing.upload_handle,
                                 _ref(data, handle=nothing.upload_handle, kind=MediaKind.upload)),
                   R.nothing_received)


# --- ADMISSION-READY -----------------------------------------------------------------------
async def admission_ready__a_text_job_is_ready_with_an_empty_manifest(factory):
    """RV-05: a text-only admission commits a marker with ZERO sources - completed, not
    missing - visible to another process, and preparation may claim it. In BOTH regimes."""
    harness = factory()
    for expectation in EXPECTATIONS:
        request = _request(harness)
        admission, readiness = await harness.port.admit_ready(
            request, _idem(request, _key("text", expectation)), expectation)
        assert readiness is not None and readiness.sources == () and \
            readiness.job_id == admission.request_id == request.request_id, expectation
        assert readiness.ready_at == admission.admitted_at
        assert await hook(harness, "reopen")().readiness(request.request_id) == readiness
        view = readiness_view(request.request_id, readiness)
        assert (view.state, view.source_count) == (ReadinessState.ready, 0)
        lease = await harness.port.claim_preparation(request.request_id, "prep-a")
        assert lease.kind is LeaseKind.preparation


async def admission_ready__a_job_with_no_marker_is_never_claimable(factory):
    """The other half of RV-05: a job the previous runtime admitted - `admit` or
    `admit_credit`, which never write a marker - reads NOT READY, never an empty
    manifest, and preparation cannot claim it."""
    harness = factory()
    for expectation in EXPECTATIONS:
        request = _request(harness)
        await _previous_admit(harness, expectation)(
            request, _idem(request, _key("previous", expectation)))
        assert await harness.port.readiness(request.request_id) is None, expectation
        assert await hook(harness, "reopen")().readiness(request.request_id) is None
        view = readiness_view(request.request_id, None)
        assert view.state is ReadinessState.not_ready and view.source_count is None
        await _refused(harness.port.claim_preparation(request.request_id, "prep-a"),
                       R.not_ready)


async def admission_ready__the_manifest_is_the_orgs_own_live_content(factory):
    """A forged manifest admits nothing: another tenant's ref, a digest unlike the content
    row, a ref never staged, a repeated source, a key that is not source content, an upload
    whose ticket names other content. A valid one records each source against its content
    row and generation. In both regimes."""
    harness = factory()
    port = harness.port
    good = await _staged(harness, b"own-clip")
    foreign = await _staged(harness, b"their-clip", org=OTHER)
    forged_digest = MediaRef.model_validate({**good.model_dump(), "digest": _digest(b"lie")})
    never = _ref(b"never-staged")
    pending = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                             allowed_mime=MP4))
    not_source = _ref(b"a-destination", handle="med_" + "d" * 40)
    await port.register(ContentIdentity(
        org_id=ORG, kind=ContentKind.upload_destination, location=ContentLocation.object_store,
        object_key=not_source.storage_ref, digest=not_source.digest, bytes=not_source.bytes,
        upload_handle=pending.upload_handle, origin=ContentOrigin.written))
    done, _upload = await _uploaded(harness, b"uploaded-clip")
    elsewhere = MediaRef.model_validate({**good.model_dump(), "kind": MediaKind.upload,
                                         "handle": done.upload_handle})
    forged = (((foreign,), R.not_found), ((forged_digest,), R.not_found),
              ((never,), R.not_found), ((good, good), R.invalid_manifest),
              ((not_source,), R.not_found), ((elsewhere,), R.not_found))
    for expectation in EXPECTATIONS:
        before = _balances(harness)
        for n, (refs, reason) in enumerate(forged):
            request = _request(harness, refs)
            idem = _idem(request, _key(f"forged-{n}", expectation))
            await _refused(port.admit_ready(request, idem, expectation), reason)
            await _nothing_admitted(harness, request, idem, before)
        request = _request(harness, (good,))
        admission, readiness = await port.admit_ready(
            request, _idem(request, _key("valid", expectation)), expectation)
        (source,) = readiness.sources
        assert source.ref == good and source.generation >= 1
        refs = await port.references(source.content_id)
        assert (admission.request_id, source.generation) in [(r.job_id, r.generation)
                                                            for r in refs]


async def admission_ready__a_refused_expectation_or_capability_admits_nothing(factory):
    """The rechecks RV-05 found after the commit run inside it: a card this runtime did
    not approve, or a pinned revision that cannot take the request's media or stream its
    output, admits nothing. (A legacy expectation has no card and no pinned revision.)"""
    harness = factory()
    port = harness.port
    before = _balances(harness)
    request = _request(harness)
    other_card = AdmissionExpectation(accounting_regime=AccountingRegime.credit,
                                      rate_card_version="rc_not_this_runtime")
    await _refused(port.admit_ready(request, _idem(request), other_card), R.expectation_mismatch)
    await _nothing_admitted(harness, request, _idem(request), before)
    clip = await _staged(harness, b"needs-video")
    for capability, mode, refs, refusal in (
            (("text",), ExecutionMode.async_, (clip,), errors.UnsupportedMedia),
            (("text", "video"), ExecutionMode.stream, (), errors.UnsupportedParameter)):
        hook(harness, "set_capability")(IDS.serving_version, capability,
                                        stream_output=mode is not ExecutionMode.stream)
        request = _request(harness, refs, mode=mode)
        idem = _idem(request, f"cap-{mode.value}")
        try:
            await port.admit_ready(request, idem, CARD)
        except refusal:
            pass
        else:
            raise AssertionError(f"the pinned revision took {capability} / {mode}")
        await _nothing_admitted(harness, request, idem, before)


async def admission_ready__a_replay_answers_the_recorded_marker(factory):
    """The same key answers the first admission and ITS marker, not a second one."""
    harness = factory()
    clip = await _staged(harness, b"replayed")
    for expectation in EXPECTATIONS:
        request = _request(harness, (clip,))
        idem = _idem(request, _key("replay", expectation))
        first = await harness.port.admit_ready(request, idem, expectation)
        again = await harness.port.admit_ready(request, idem, expectation)
        assert again[0].replayed and again[0].request_id == first[0].request_id
        assert again[1] == first[1], "a replay recorded another marker"


async def admission_ready__an_expired_upload_cannot_be_admitted(factory):
    """R99(b) at the admission boundary: a finalized upload past its window admits
    nothing, however the request reached the store."""
    harness = factory()
    before = _balances(harness)
    done, ref = await _uploaded(harness, b"late-upload")
    _at(harness, done.expires_at)
    for expectation in EXPECTATIONS:
        request = _request(harness, (ref,))
        idem = _idem(request, _key("late", expectation))
        await _refused(harness.port.admit_ready(request, idem, expectation), R.upload_expired)
        await _nothing_admitted(harness, request, idem, before)


# --- RETENTION-DURABLE ---------------------------------------------------------------------
async def _eligible(harness, ref: MediaRef):
    row = await harness.port.register(_written(ref))
    _at(harness, row.eligible_at)
    return row


async def _all_candidates(port) -> list:
    items, after = [], None
    while True:
        page = await port.candidates(after=after, limit=2)
        items.extend(page.items)
        if page.next_cursor is None:
            return items
        after = page.next_cursor


async def retention_durable__a_referenced_object_is_neither_a_candidate_nor_claimable(factory):
    """A source an admitted job executes on is protected while the job runs and, after it
    ends, until the reference's persisted `retain_until` - then it is a candidate."""
    harness = factory()
    port = harness.port
    clip = await _staged(harness, b"in-use")
    request = _request(harness, (clip,))
    admission, readiness = await port.admit_ready(request, _idem(request), CARD)
    (source,) = readiness.sources
    row = await port.register(_written(clip))
    _at(harness, row.eligible_at)
    assert source.content_id not in {r.content_id for r in await _all_candidates(port)}
    await _refused(port.claim(source.content_id, source.generation, "sweeper-a"),
                   R.reference_live)
    await hook(harness, "jobs").cancel(ORG, admission.job_handle)
    (ref,) = await port.references(source.content_id)
    assert ref.retain_until is not None and ref.retain_until > harness.clock.now()
    await _refused(port.claim(source.content_id, source.generation, "sweeper-a"),
                   R.reference_live)
    _at(harness, ref.retain_until)
    assert source.content_id in {r.content_id for r in await _all_candidates(port)}


async def retention_durable__admission_wins_over_a_claim_that_has_not_tombstoned(factory):
    """attach/delete, admission first: a claim does not block use, and the tombstone's
    recheck - under the same lock - sees the new reference and keeps the object."""
    harness = factory()
    port = harness.port
    clip = await _staged(harness, b"raced")
    row = await _eligible(harness, clip)
    claim = await port.claim(row.content_id, row.generation, "sweeper-a")
    request = _request(harness, (clip,))
    _admission, readiness = await port.admit_ready(request, _idem(request), CARD)
    assert readiness.sources[0].content_id == row.content_id
    await _refused(port.tombstone(claim), R.reference_live)
    assert (await port.register(_written(clip))).state is LifecycleState.live


async def retention_durable__a_tombstone_refuses_new_use_until_the_delete_is_acked(factory):
    """attach/delete, delete first: once tombstoned, admission and re-registration wait
    (`content_retiring`); after the acknowledgement the key is a NEW generation, and a
    delayed acknowledgement of the old tombstone cannot retire it."""
    harness = factory()
    port = harness.port
    before = _balances(harness)
    clip = await _staged(harness, b"retired")
    row = await _eligible(harness, clip)
    tombstone = await port.tombstone(await port.claim(row.content_id, row.generation, "s-a"))
    assert tombstone.generation == row.generation
    request = _request(harness, (clip,))
    await _refused(port.admit_ready(request, _idem(request), CARD), R.content_retiring)
    await _nothing_admitted(harness, request, _idem(request), before)
    await _refused(port.register(_written(clip)), R.content_retiring)
    deleted = await port.acknowledge_delete(tombstone)
    assert deleted.state is LifecycleState.deleted
    assert await port.acknowledge_delete(tombstone) == deleted, "a repeated ack is not idempotent"
    request = _request(harness, (clip,))
    await _refused(port.admit_ready(request, _idem(request, "deleted"), CARD), R.not_found)
    reborn = await port.register(_written(clip))
    assert (reborn.content_id, reborn.generation, reborn.state) == (
        row.content_id, row.generation + 1, LifecycleState.live)
    assert await port.acknowledge_delete(tombstone) == reborn, "a delayed ack retired it"
    request = _request(harness, (clip,))
    _admission, readiness = await port.admit_ready(request, _idem(request, "reborn"), CARD)
    assert readiness.sources[0].generation == reborn.generation


async def retention_durable__claims_are_leased_and_fenced(factory):
    """Two sweepers: one unexpired claim at a time; a lapsed claim is superseded by a
    higher fence; the superseded holder can neither tombstone nor acknowledge."""
    harness = factory()
    port = harness.port
    clip = await _staged(harness, b"fenced")
    fresh = await port.register(_written(clip))
    await _refused(port.claim(fresh.content_id, fresh.generation, "s-a"), R.not_eligible)
    row = await _eligible(harness, clip)
    await _refused(port.claim(row.content_id, row.generation + 1, "s-a"), R.claim_lost)
    first = await port.claim(row.content_id, row.generation, "s-a")
    assert row.content_id not in {r.content_id for r in await _all_candidates(port)}, \
        "a row under an unexpired claim is offered to another sweeper"
    await _refused(port.claim(row.content_id, row.generation, "s-b"), R.claim_held)
    _at(harness, first.expires_at)
    await _refused(port.tombstone(first), R.claim_lost)
    second = await port.claim(row.content_id, row.generation, "s-b")
    assert second.fence > first.fence
    tombstone = await port.tombstone(second)
    stale = Tombstone.model_validate({**tombstone.model_dump(), "fence": first.fence})
    await _refused(port.acknowledge_delete(stale), R.claim_lost)
    assert (await port.acknowledge_delete(tombstone)).state is LifecycleState.deleted


async def retention_durable__an_unfinished_delete_is_reconciled_by_a_higher_fence(factory):
    """A sweeper that tombstoned and died before acknowledging does not strand the key:
    while its claim is live nobody else touches the row; once it lapses the tombstoned row
    is a candidate again, a new holder re-claims it with a higher fence, finishes the
    delete, and the key can be registered again as the next generation."""
    harness = factory()
    port = harness.port
    clip = await _staged(harness, b"crashed-sweeper")
    row = await _eligible(harness, clip)
    first = await port.claim(row.content_id, row.generation, "s-dead")
    await port.tombstone(first)                        # ... and the holder dies here
    assert row.content_id not in {r.content_id for r in await _all_candidates(port)}
    await _refused(port.claim(row.content_id, row.generation, "s-b"), R.claim_held)
    _at(harness, first.expires_at)
    offered = {r.content_id: r for r in await _all_candidates(port)}
    assert row.content_id in offered, "an unfinished delete is never offered again"
    assert offered[row.content_id].state is LifecycleState.tombstoned
    second = await port.claim(row.content_id, row.generation, "s-b")
    assert second.fence > first.fence
    tombstone = await port.tombstone(second)
    assert tombstone.fence == second.fence
    assert (await port.acknowledge_delete(tombstone)).state is LifecycleState.deleted
    assert (await port.register(_written(clip))).generation == row.generation + 1


async def retention_durable__eligibility_survives_restart_and_discovery(factory):
    """Grace is persisted at the first registration: neither a new process nor a
    collector re-registering the key as `discovered` resets it, and an object first
    found by discovery starts its own grace rather than being deletable at once."""
    harness = factory()
    port = harness.port
    clip = await _staged(harness, b"persisted")
    row = await port.register(_written(clip))
    assert row.eligible_at > harness.clock.now()
    assert row.content_id not in {r.content_id for r in await _all_candidates(port)}
    later = hook(harness, "reopen")()
    harness.clock.advance(1)
    rediscovered = await later.register(ContentIdentity.model_validate(
        {**_written(clip).model_dump(), "origin": ContentOrigin.discovered}))
    assert rediscovered.eligible_at == row.eligible_at, "a restart reset the grace"
    await _refused(later.register(ContentIdentity.model_validate(
        {**_written(clip).model_dump(), "org_id": OTHER})), R.not_found)
    await _refused(later.register(ContentIdentity.model_validate(
        {**_written(clip).model_dump(), "digest": _digest(b"other bytes")})), R.bytes_changed)
    orphan = await later.register(ContentIdentity(
        org_id=ORG, kind=ContentKind.source, location=ContentLocation.object_store,
        object_key=f"media/{ORG}/v1/0123456789abcdef/source", origin=ContentOrigin.discovered))
    assert orphan.eligible_at > harness.clock.now()
    _at(harness, row.eligible_at)
    found = {r.content_id for r in await _all_candidates(later)}
    assert row.content_id in found and (orphan.content_id in found) == (
        orphan.eligible_at <= row.eligible_at)


async def retention_durable__an_upload_protects_its_destination_and_source(factory):
    """RV-03's upload half: the destination is referenced by its OPEN ticket, the
    finalized source by its unexpired ticket - durable state, not a process's dict."""
    harness = factory()
    port = harness.port
    pending = await port.create(ORG, UploadConstraints.parse({}, max_media_bytes=1 << 26,
                                                             allowed_mime=MP4))
    destination = await port.register(ContentIdentity(
        org_id=ORG, kind=ContentKind.upload_destination, location=ContentLocation.object_store,
        object_key=f"uploads/{ORG}/{pending.upload_handle}", digest=_digest(b"d"), bytes=1,
        upload_handle=pending.upload_handle, origin=ContentOrigin.written))
    done, _ref_ = await _uploaded(harness, b"kept-while-usable")
    source = done.finalized
    assert destination.eligible_at < pending.expires_at, "fixture: grace < upload window"
    _at(harness, destination.eligible_at)
    await _refused(port.claim(destination.content_id, destination.generation, "s"),
                   R.reference_live)
    await _refused(port.claim(source.content_id, source.generation, "s"), R.reference_live)
    _at(harness, max(pending.expires_at, done.expires_at))
    found = {r.content_id for r in await _all_candidates(port)}
    assert {destination.content_id, source.content_id} <= found


async def retention_durable__a_payload_is_protected_by_its_job_once_admitted(factory):
    """Staged payload envelopes are content too: before admission nothing references one
    (a refused request's envelope is collectable), afterwards its job does."""
    harness = factory()
    port = harness.port
    request, orphaned = _request(harness), _request(harness)

    def payload(req):
        return ContentIdentity(org_id=ORG, kind=ContentKind.payload,
                               location=ContentLocation.object_store,
                               object_key=f"payloads/{ORG}/{req.request_id}.json",
                               digest=_digest(req.request_id.encode()), bytes=64,
                               job_id=req.request_id, origin=ContentOrigin.written)
    kept, dropped = await port.register(payload(request)), await port.register(payload(orphaned))
    await port.admit_ready(request, _idem(request), CARD)
    _at(harness, max(kept.eligible_at, dropped.eligible_at))
    await _refused(port.claim(kept.content_id, kept.generation, "s"), R.reference_live)
    claim = await port.claim(dropped.content_id, dropped.generation, "s")
    assert claim.fence >= 1


async def retention_durable__candidates_page_without_loss_or_repeat(factory):
    """Keyset pages over persisted eligibility: every candidate exactly once, an opaque
    cursor, a bounded limit."""
    harness = factory()
    port = harness.port
    rows = [await port.register(_written(_ref(f"page-{n}".encode()))) for n in range(5)]
    _at(harness, max(row.eligible_at for row in rows))
    seen = [r.content_id for r in await _all_candidates(port)]
    assert sorted(seen) == sorted(r.content_id for r in rows) and len(set(seen)) == len(seen)
    try:
        await port.candidates(after=None, limit=0)
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("limit 0 accepted")
    more = [await port.register(_written(_ref(f"bulk-{n}".encode())))
            for n in range(MAX_PAGE - len(rows) + 1)]
    _at(harness, max(row.eligible_at for row in more))
    page = await port.candidates(after=None, limit=MAX_PAGE + 5)
    assert len(page.items) == MAX_PAGE and page.next_cursor is not None, "the page is unbounded"
    try:
        await port.candidates(after="not-a-cursor", limit=2)
    except errors.InvalidCursor:
        pass
    else:
        raise AssertionError("a forged cursor accepted")


# --- RESULT-EXPIRY (F2C.b) --------------------------------------------------------------------
async def _succeeded(harness, key: str, *, proposal_expiry=None, before_complete=None):
    """Admit ready, prepare, claim and complete one CREDIT job; the committed outcome."""
    port, jobs = harness.port, hook(harness, "jobs")
    request = _request(harness)
    admission, _ready = await port.admit_ready(request, _idem(request, key), CARD)
    await jobs.prepared(await port.claim_preparation(request.request_id, "prep-a"))
    lease = await jobs.claim(request.request_id, "worker-a")
    proposal = b.outcome(request.request_id, harness)
    if proposal_expiry is not None:
        proposal = TerminalOutcome.model_validate(
            {**proposal.model_dump(), "result_expires_at": proposal_expiry})
    if before_complete is not None:
        await before_complete(request)
    outcome, _settlement = await jobs.complete_credit(lease, proposal)
    return request, admission, outcome


async def result_expiry__a_committed_success_carries_its_persisted_expiry(factory):
    """RV-11: the settling transaction persists the result's expiry and every committed
    read (owned read, idempotent lookup) carries that same instant; the store clock at it
    reads `expired`. Every other outcome carries none."""
    harness = factory()
    jobs = hook(harness, "jobs")
    request, admission, outcome = await _succeeded(harness, "expiring")
    assert outcome.result_expires_at is not None and outcome.result_expires_at > outcome.settled_at
    assert (await jobs.get_owned_credit(ORG, admission.job_handle))[1] == outcome
    assert (await jobs.lookup(ORG, _idem(request, "expiring")))[1] == outcome
    assert read_outcome(outcome, harness.clock.now()) is ReadOutcome.available
    _at(harness, outcome.result_expires_at)
    assert read_outcome((await jobs.get_owned_credit(ORG, admission.job_handle))[1],
                        harness.clock.now()) is ReadOutcome.expired
    cancelled = _request(harness)
    other, _ = await harness.port.admit_ready(cancelled, _idem(cancelled, "cancelled"), CARD)
    ended = await jobs.cancel(ORG, other.job_handle)
    assert ended.result_expires_at is None and \
        read_outcome(ended, harness.clock.now()) is ReadOutcome.no_result


async def result_expiry__the_proposal_never_selects_the_expiry(factory):
    """No caller-selected expiry: a worker's proposal naming one is not honoured."""
    harness = factory()
    far = harness.clock.now().replace(year=2099)
    _request_, _admission, outcome = await _succeeded(harness, "far", proposal_expiry=far)
    assert outcome.result_expires_at is not None and outcome.result_expires_at < far


async def result_expiry__a_configuration_change_never_moves_a_promised_expiry(factory):
    """The promise is the persisted instant: changing the configured TTL later moves no
    committed result (it applies to settlements after it). Re-deriving the expiry from
    current configuration - what `Jobs.result_expiry` does today - fails here."""
    harness = factory()
    jobs = hook(harness, "jobs")
    _r, admission, first = await _succeeded(harness, "before")
    hook(harness, "retune")(result_ttl_s=60.0)
    again = (await jobs.get_owned_credit(ORG, admission.job_handle))[1]
    assert again.result_expires_at == first.result_expires_at, "a retune moved a promise"
    _r2, _a2, second = await _succeeded(harness, "after")
    assert (second.result_expires_at - second.settled_at).total_seconds() == 60.0


async def result_expiry__a_result_is_kept_to_its_expiry_then_scrubbed_not_forgotten(factory):
    """D3: the result body is content (`result`, in the database). It is protected until
    the PERSISTED expiry - however the TTL is reconfigured meanwhile - then scrubbed
    through claim/tombstone/acknowledgement; the job, its idempotency mapping, outcome,
    usage and settlement stay, and the read is `expired`, never regenerated."""
    harness = factory()
    port, jobs = harness.port, hook(harness, "jobs")
    hook(harness, "retune")(result_ttl_s=3600.0)
    registered = {}

    async def put_result(request):          # the result is stored BEFORE settlement (02 §7)
        registered["row"] = await port.register(ContentIdentity(
            org_id=ORG, kind=ContentKind.result, location=ContentLocation.database,
            object_key=f"job_results/{request.request_id}", digest=_digest(b"text"),
            bytes=4, job_id=request.request_id, origin=ContentOrigin.written))
    request, admission, outcome = await _succeeded(harness, "scrubbed",
                                                   before_complete=put_result)
    row = registered["row"]
    before = _balances(harness)
    hook(harness, "retune")(result_ttl_s=1.0)
    _at(harness, max(row.eligible_at, outcome.result_expires_at - timedelta(seconds=1)))
    assert harness.clock.now() < outcome.result_expires_at, "fixture: grace < result TTL"
    await _refused(port.claim(row.content_id, row.generation, "s"), R.reference_live)
    _at(harness, outcome.result_expires_at)
    assert row.content_id in {r.content_id for r in await _all_candidates(port)}
    tombstone = await port.tombstone(await port.claim(row.content_id, row.generation, "s"))
    assert (await port.acknowledge_delete(tombstone)).state is LifecycleState.deleted
    _admission, kept = await jobs.get_owned_credit(ORG, admission.job_handle)
    assert kept == outcome, "scrubbing content changed the terminal outcome"
    assert read_outcome(kept, harness.clock.now()) is ReadOutcome.expired
    assert (await jobs.lookup(ORG, _idem(request, "scrubbed")))[1] == outcome
    assert _balances(harness) == before, "scrubbing moved money"


def cases() -> list[Callable]:
    return [
        upload_restart__every_step_survives_a_new_process,
        upload_restart__the_tenant_handle_and_window_are_the_stores,
        upload_restart__one_handle_names_one_set_of_bytes,
        upload_restart__the_window_bounds_completion_and_use,
        upload_restart__oversize_puts_and_aborts_are_final_everywhere,
        upload_restart__a_failed_check_is_final,
        admission_ready__a_text_job_is_ready_with_an_empty_manifest,
        admission_ready__a_job_with_no_marker_is_never_claimable,
        admission_ready__the_manifest_is_the_orgs_own_live_content,
        admission_ready__a_refused_expectation_or_capability_admits_nothing,
        admission_ready__a_replay_answers_the_recorded_marker,
        admission_ready__an_expired_upload_cannot_be_admitted,
        retention_durable__a_referenced_object_is_neither_a_candidate_nor_claimable,
        retention_durable__admission_wins_over_a_claim_that_has_not_tombstoned,
        retention_durable__a_tombstone_refuses_new_use_until_the_delete_is_acked,
        retention_durable__claims_are_leased_and_fenced,
        retention_durable__an_unfinished_delete_is_reconciled_by_a_higher_fence,
        retention_durable__eligibility_survives_restart_and_discovery,
        retention_durable__an_upload_protects_its_destination_and_source,
        retention_durable__a_payload_is_protected_by_its_job_once_admitted,
        retention_durable__candidates_page_without_loss_or_repeat,
        result_expiry__a_committed_success_carries_its_persisted_expiry,
        result_expiry__the_proposal_never_selects_the_expiry,
        result_expiry__a_configuration_change_never_moves_a_promised_expiry,
        result_expiry__a_result_is_kept_to_its_expiry_then_scrubbed_not_forgotten,
    ]


def run_lifecycle_conformance(factory) -> int:
    """Run every lifecycle case against `factory() -> Harness`; raises on failure."""
    from . import run_cases
    return run_cases(cases(), factory)


__all__ = ["cases", "run_lifecycle_conformance"]
