"""Conformance for MediaStore, Scheduler, Engine, TraceSink, FeedbackService and
JudgeCoordinator: MEDIA-SEC/PARITY shapes, DUR-OUTBOX index rules, API-STREAM
engine faults, TRACE-BOUNDS, FEEDBACK-ACK and JUDGE-BUDGET.
"""
from __future__ import annotations

from decimal import Decimal

from .. import errors
from ..limits import DEFAULTS
from ..records import (AuthorRole, ChunkEventType, FeedbackChannel, FeedbackName,
                       JudgeResolution, JudgeRunState, MediaKind, Role, TraceLossReason,
                       TraceMode, TraceOfferResult)
from . import builders as b
from .harness import hook

# ==========================================================================
# MediaStore
# ==========================================================================
async def media_sec__an_upload_is_owned_verified_and_immutable(factory):
    """MEDIA-SEC: the digest and size of a finalized upload are verified, not
    trusted, and completion is idempotent."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {"max_bytes": 1024,
                                                        "accepted_mime": ("video/mp4",)})
    handle = ticket["upload_handle"]
    assert handle.startswith("upl_") and str(ticket["destination_ref"]).startswith("infrx-upload:")
    assert "?" not in str(ticket["destination_ref"])          # not a signed URL
    hook(harness, "put_object")(handle, b"0123456789", "video/mp4")
    ref = await harness.port.finalize_upload(b.ORG_A, handle)
    assert ref.bytes == 10 and ref.digest.startswith("sha256:") and ref.org_id == b.ORG_A
    assert await harness.port.finalize_upload(b.ORG_A, handle) == ref
    assert await harness.port.resolve_owned(b.ORG_A, handle) == ref


async def media_sec__another_org_cannot_resolve_or_finalize(factory):
    """MEDIA-SEC: cross-tenant media access is a 404, never a 403 hint."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {})
    handle = ticket["upload_handle"]
    hook(harness, "put_object")(handle, b"bytes", "video/mp4")
    await harness.port.finalize_upload(b.ORG_A, handle)
    for call in (harness.port.finalize_upload(b.ORG_B, handle),
                 harness.port.resolve_owned(b.ORG_B, handle)):
        try:
            await call
        except errors.NotFound:
            pass
        else:
            raise AssertionError("another org reached the media object")


async def media_sec__oversize_and_unsupported_uploads_are_refused(factory):
    """MEDIA-SEC: bounded bytes and an allow-list of types, before any use."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {"max_bytes": 8,
                                                        "accepted_mime": ("video/mp4",)})
    handle = ticket["upload_handle"]
    hook(harness, "put_object")(handle, b"much too large", "video/mp4")
    try:
        await harness.port.finalize_upload(b.ORG_A, handle)
    except errors.RequestTooLarge as exc:
        assert errors.http_status(exc.code) == 413
    else:
        raise AssertionError("an oversize upload was finalized")
    other = await harness.port.create_upload(b.ORG_A, {"accepted_mime": ("video/mp4",)})
    hook(harness, "put_object")(other["upload_handle"], b"tiny", "application/zip")
    try:
        await harness.port.finalize_upload(b.ORG_A, other["upload_handle"])
    except errors.UnsupportedMedia:
        pass
    else:
        raise AssertionError("an unsupported media type was finalized")
    for constraints in ({"max_bytes": DEFAULTS.max_media_bytes * 2}, {"max_bytes": 0},
                        {"max_bytes": -1}, {"max_bytes": "4096"}, {"max_bytes": 1.5},
                        {"accepted_mime": ()}):
        try:
            await harness.port.create_upload(b.ORG_A, constraints)
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"a caller shaped its own constraints: {constraints}")


async def media_parity__staging_is_content_addressed_and_tenant_namespaced(factory):
    """MEDIA-PARITY: the storage key comes from the tenant, digest and profile;
    the caller never names a path, and two orgs never share an object."""
    harness = factory()
    request_a = b.request(harness, refs=(b.media(b.ORG_A),))
    request_b = b.request(harness, org_id=b.ORG_B, refs=(b.media(b.ORG_B),))
    staged_a = await harness.port.stage(b.ORG_A, request_a)
    staged_b = await harness.port.stage(b.ORG_B, request_b)
    assert staged_a[0].storage_ref != staged_b[0].storage_ref
    assert b.ORG_A in staged_a[0].storage_ref and b.ORG_B in staged_b[0].storage_ref
    hook(harness, "attach")("job_stagingfixture", staged_a)
    prepared = await harness.port.prepare("job_stagingfixture", "profile-2")
    assert prepared[0].profile_version == "profile-2"
    assert prepared[0].storage_ref != staged_a[0].storage_ref
    # the profile version namespaces the cache (01: "tenant source digest + profile
    # version namespace both media cache keys"), so it is *in* the key
    assert "profile-2" in prepared[0].storage_ref
    assert b.ORG_A in prepared[0].storage_ref
    assert prepared[0].digest == staged_a[0].digest            # same source content


async def media_sec__a_foreign_media_reference_is_not_staged(factory):
    """MEDIA-SEC: a JSON body cannot name another tenant's object."""
    harness = factory()
    request = b.request(harness, org_id=b.ORG_A, refs=(b.media(b.ORG_B),))
    try:
        await harness.port.stage(b.ORG_A, request)
    except errors.NotFound:
        pass
    else:
        raise AssertionError("a cross-tenant media reference was staged")
    big = b.media(b.ORG_A, nbytes=DEFAULTS.max_media_bytes + 1)
    try:
        await harness.port.stage(b.ORG_A, b.request(harness, refs=(big,)))
    except errors.RequestTooLarge:
        pass
    else:
        raise AssertionError("an oversize source was staged")


async def media_sec__staging_never_replaces_an_existing_object(factory):
    """MEDIA-SEC: finalized and staged content is immutable and tenant scoped. A
    staged reference can neither overwrite an object nor name another org's, so a
    colliding handle cannot make the owner's upload disappear."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {"max_bytes": 1024})
    handle = ticket["upload_handle"]
    hook(harness, "put_object")(handle, b"the original bytes", "video/mp4")
    owned = await harness.port.finalize_upload(b.ORG_A, handle)

    # another tenant staging the same handle touches nothing of org A's
    foreign = b.media(b.ORG_B, handle=handle, kind=MediaKind.inline)
    staged = await harness.port.stage(b.ORG_B, b.request(harness, org_id=b.ORG_B,
                                                         refs=(foreign,)))
    assert await harness.port.resolve_owned(b.ORG_A, handle) == owned
    assert staged[0].storage_ref != owned.storage_ref

    # and the owner cannot rewrite it either: immutable content, or a conflict
    clash = b.media(b.ORG_A, handle=handle, kind=MediaKind.inline)
    try:
        await harness.port.stage(b.ORG_A, b.request(harness, refs=(clash,)))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 404, 409), exc.code
    else:
        raise AssertionError("staging overwrote an immutable object")
    assert await harness.port.resolve_owned(b.ORG_A, handle) == owned

    # restaging identical content is idempotent, not a second object
    ref = b.media(b.ORG_A)
    once = await harness.port.stage(b.ORG_A, b.request(harness, refs=(ref,)))
    assert await harness.port.stage(b.ORG_A, b.request(harness, refs=(ref,))) == once

    # nor does finalizing an upload replace what that handle already holds *within*
    # the tenant: the immutability claim held across orgs and for `stage`, but a
    # tenant's own object staged under a not-yet-finalized upload handle could be
    # overwritten by completing that upload with different bytes.
    reused = await harness.port.create_upload(b.ORG_A, {"max_bytes": 1024})
    second = reused["upload_handle"]
    squatted = b.media(b.ORG_A, handle=second, kind=MediaKind.inline)
    staged_first = await harness.port.stage(b.ORG_A, b.request(harness, refs=(squatted,)))
    hook(harness, "put_object")(second, b"different bytes entirely", "video/mp4")
    try:
        finalized = await harness.port.finalize_upload(b.ORG_A, second)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 404, 409), exc.code
    else:
        assert finalized.digest == staged_first[0].digest, \
            "finalizing replaced an object the tenant already had"
    try:
        current = await harness.port.resolve_owned(b.ORG_A, second)
    except errors.DomainError:
        pass                      # a handle left unfinalized may refuse resolution
    else:
        assert current.digest == staged_first[0].digest, \
            "the staged object was replaced by the completed upload"


async def media_sec__a_refused_upload_stays_refused(factory):
    """MEDIA-SEC: an upload aborted by a failed check is final. Re-finalizing it would
    be a second attempt at the same size and type checks, and a declared checksum is
    verified against the bytes that actually arrived (01: "completion verifies object
    metadata/checksum and ownership before use")."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {"max_bytes": 8})
    handle = ticket["upload_handle"]
    hook(harness, "put_object")(handle, b"far too many bytes", "video/mp4")
    try:
        await harness.port.finalize_upload(b.ORG_A, handle)
    except errors.RequestTooLarge:
        pass
    else:
        raise AssertionError("an oversize upload was finalized")
    hook(harness, "put_object")(handle, b"tiny", "video/mp4")      # now within the limit
    try:
        await harness.port.finalize_upload(b.ORG_A, handle)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 404, 409), exc.code
    else:
        raise AssertionError("an aborted upload was finalized on a second attempt")

    # a declared digest is checked against the bytes that arrived
    declared = await harness.port.create_upload(
        b.ORG_A, {"max_bytes": 1024, "digest": "sha256:" + "0" * 64})
    other = declared["upload_handle"]
    hook(harness, "put_object")(other, b"not what was promised", "video/mp4")
    try:
        await harness.port.finalize_upload(b.ORG_A, other)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 409), exc.code
    else:
        raise AssertionError("an upload was finalized against a digest it does not have")
    for constraints in ({"accepted_mime": "video/mp4"},        # a string is not a list
                        {"accepted_mime": 5},                   # nor is a number
                        {"accepted_mime": None},
                        {"maxbytes": 1024},                     # a rule nobody enforces
                        {"digest": "deadbeef"}):
        try:
            await harness.port.create_upload(b.ORG_A, constraints)
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"constraints {constraints} were accepted")


async def media_sec__an_expired_upload_window_says_so(factory):
    """MEDIA-SEC / r1 R22: a closed upload window answers `410 upload_expired`
    ("The upload window has expired."), never `result_expired`: a customer told their
    *result* is gone would go looking for a job that never existed."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {"max_bytes": 1024})
    handle = ticket["upload_handle"]
    hook(harness, "put_object")(handle, b"in time", "video/mp4")
    harness.clock.advance(DEFAULTS.processing_cache_ttl_s + 1)
    try:
        await harness.port.finalize_upload(b.ORG_A, handle)
    except errors.DomainError as exc:
        assert exc.code == "upload_expired", exc.code
        assert errors.http_status(exc.code) == 410
        assert errors.error_type(exc.code) == "gone_error"
        assert errors.MESSAGES[exc.code] == "The upload window has expired."
    else:
        raise AssertionError("an expired upload window still finalized")
    try:
        await harness.port.resolve_owned(b.ORG_A, handle)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (400, 404, 410), exc.code
    else:
        raise AssertionError("an expired upload resolved to an object")


async def media_sec__a_partial_request_stages_nothing(factory):
    """MEDIA-SEC: staging is all or nothing (02: "a staging failure creates no job or
    hold"). Whatever refuses the request - an oversize source, another tenant's
    reference, or an upload handle that cannot be **resolved** - the first item must
    not be left behind, or a client correcting the bad reference and retrying finds
    half its request already stored under a handle it can no longer change."""
    harness = factory()
    good = b.media(b.ORG_A, handle="upl_partialfixture00000000000000000000001")
    refusals = (
        b.media(b.ORG_A, handle="upl_partialfixture00000000000000000000002",
                nbytes=DEFAULTS.max_media_bytes + 1),                 # oversize
        b.media(b.ORG_B, handle="upl_partialfixture00000000000000000000003"),  # not ours
        b.media(b.ORG_A, handle="upl_partialfixture00000000000000000000004",
                kind=MediaKind.upload),                               # unresolvable upload
    )
    for n, bad in enumerate(refusals):
        request = b.request(harness, refs=(good, bad))
        try:
            await harness.port.stage(b.ORG_A, request)
        except errors.DomainError as exc:
            assert errors.http_status(exc.code) in (400, 403, 404, 409, 413), exc.code
        else:
            raise AssertionError(f"refusal {n} was staged anyway")
        try:
            await harness.port.resolve_owned(b.ORG_A, good.handle)
        except errors.NotFound:
            pass
        else:
            raise AssertionError(f"refusal {n} left the first media item staged")
    # the same handle twice in one request, with different content, is a 400: last-wins
    # would stage one object and hand the job the other one's digest
    twin = good.model_copy(update={"digest": b.digest("different content entirely"),
                                   "bytes": good.bytes + 1})
    try:
        await harness.port.stage(b.ORG_A, b.request(harness, refs=(good, twin)))
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("one handle carried two different objects in one request")
    try:
        await harness.port.resolve_owned(b.ORG_A, good.handle)
    except errors.NotFound:
        pass
    else:
        raise AssertionError("the duplicate-handle request staged something")
    # and the corrected retry stages cleanly
    staged = await harness.port.stage(b.ORG_A, b.request(harness, refs=(good,)))
    assert staged[0].handle == good.handle
    assert await harness.port.resolve_owned(b.ORG_A, good.handle) == staged[0]


def mediastore_cases():
    return [media_sec__an_upload_is_owned_verified_and_immutable,
            media_sec__another_org_cannot_resolve_or_finalize,
            media_sec__oversize_and_unsupported_uploads_are_refused,
            media_parity__staging_is_content_addressed_and_tenant_namespaced,
            media_sec__a_foreign_media_reference_is_not_staged,
            media_sec__staging_never_replaces_an_existing_object,
            media_sec__a_refused_upload_stays_refused,
            media_sec__an_expired_upload_window_says_so,
            media_sec__a_partial_request_stages_nothing]


# ==========================================================================
# Scheduler
# ==========================================================================
def _index_event(harness, *, job_id=None, attempt=0):
    from ..records import ExecutionMode, IndexEvent
    return IndexEvent(event_id=harness.ids.event_id(), job_id=job_id or harness.ids.uuid(),
                      org_id=b.ORG_A, key_id=b.KEY_A, execution_mode=ExecutionMode.async_,
                      available_at=harness.clock.now(), attempt=attempt)


async def dur_outbox__enqueue_is_replay_safe(factory):
    """DUR-OUTBOX: at-least-once delivery of the same stable event id indexes one
    candidate, so a replayed outbox never duplicates executable work."""
    harness = factory()
    event = _index_event(harness)
    assert await harness.port.enqueue(event) is True
    assert await harness.port.enqueue(event) is False
    first = await harness.port.claim_candidate("worker-a")
    assert first is not None and first.event_id == event.event_id
    assert await harness.port.claim_candidate("worker-b") is None


async def dur_outbox__a_claimed_candidate_is_not_re_indexed(factory):
    """DUR-OUTBOX: at-least-once delivery means the same event id arrives again while
    the first copy is **in flight**. Re-indexing it would hand one job to two workers at
    once; they would both race `JobStore.claim`, and one would always have done its
    preparation for nothing."""
    harness = factory()
    event = _index_event(harness)
    assert await harness.port.enqueue(event) is True
    claimed = await harness.port.claim_candidate("worker-a")
    assert claimed is not None and claimed.event_id == event.event_id
    assert await harness.port.enqueue(event) is False, "a claimed event was re-indexed"
    assert await harness.port.claim_candidate("worker-b") is None, \
        "the same candidate was handed to two workers"
    # after the visibility timeout it is one candidate again, not two
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    again = await harness.port.claim_candidate("worker-b")
    assert again is not None and again.event_id == event.event_id
    assert await harness.port.claim_candidate("worker-c") is None
    await harness.port.acknowledge(again)
    assert await harness.port.enqueue(event) is False        # and never after the ack
    assert await harness.port.claim_candidate("worker-c") is None


async def dur_outbox__the_index_never_authorizes_execution(factory):
    """DUR-OUTBOX: claiming a candidate mutates no durable job state; only
    JobStore.claim decides the winner."""
    harness = factory()
    jobs = hook(harness, "jobs")
    from .jobs import _admit
    from dataclasses import replace
    inner = replace(harness, port=jobs)
    request, admission = await _admit(inner)
    await jobs.prepared(admission.job_handle, ())
    await harness.port.enqueue(_index_event(harness, job_id=request.request_id))
    candidate = await harness.port.claim_candidate("worker-a")
    stored, outcome = await jobs.get_owned(request.org_id, admission.job_handle)
    assert candidate is not None and stored.state.value == "queued" and outcome is None
    lease = await jobs.claim(request.request_id, "worker-a")
    assert lease.generation == 1


async def dur_outbox__acknowledged_candidates_do_not_come_back(factory):
    """DUR-OUTBOX: an acknowledgment removes the candidate for good; the source of
    truth stays in PostgreSQL, so the ack erases no durable fact."""
    harness = factory()
    event = _index_event(harness)
    await harness.port.enqueue(event)
    claimed = await harness.port.claim_candidate("worker-a")
    await harness.port.acknowledge(claimed)
    assert await harness.port.claim_candidate("worker-a") is None
    assert await harness.port.enqueue(event) is False       # and never re-indexed


async def dur_outbox__a_lost_worker_returns_its_candidate(factory):
    """DUR-OUTBOX: index visibility is a timeout measured from the claim, not from
    the event's own timestamp. An event that has been waiting longer than the lease
    TTL must still be handed to one worker at a time, or two workers race for every
    backlogged candidate and one always loses at `JobStore.claim`."""
    harness = factory()
    stale = _index_event(harness)
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)      # the event waited in the index
    await harness.port.enqueue(stale)
    first = await harness.port.claim_candidate("worker-a")
    assert first is not None and first.event_id == stale.event_id
    assert await harness.port.claim_candidate("worker-b") is None, \
        "a backlogged candidate was handed to two workers at once"
    harness.clock.advance(DEFAULTS.lease_ttl_s + 1)
    again = await harness.port.claim_candidate("worker-b")
    assert again is not None and again.event_id == first.event_id


async def dur_outbox__rebuild_restores_every_queued_job_exactly_once(factory):
    """DUR-OUTBOX: losing the index loses throughput, never jobs."""
    harness = factory()
    snapshot = tuple(_index_event(harness) for _ in range(3))
    for event in snapshot:
        await harness.port.enqueue(event)
    claimed = await harness.port.claim_candidate("worker-a")
    assert claimed is not None                     # in flight, deliberately not removed
    assert await harness.port.rebuild(snapshot) == 3
    seen = set()
    while (candidate := await harness.port.claim_candidate("worker-a")) is not None:
        assert candidate.event_id not in seen
        seen.add(candidate.event_id)
    assert seen == {event.event_id for event in snapshot}
    # the rebuild replaced the index: no pre-rebuild in-flight entry comes back later
    harness.clock.advance(DEFAULTS.lease_ttl_s * 2 + 1)
    returned = set()
    while (candidate := await harness.port.claim_candidate("worker-b")) is not None:
        assert candidate.event_id not in returned, "an event came back twice after rebuild"
        returned.add(candidate.event_id)
    assert returned == seen, "the rebuilt index lost or duplicated a job"


def scheduler_cases():
    return [dur_outbox__enqueue_is_replay_safe,
            dur_outbox__a_claimed_candidate_is_not_re_indexed,
            dur_outbox__the_index_never_authorizes_execution,
            dur_outbox__acknowledged_candidates_do_not_come_back,
            dur_outbox__a_lost_worker_returns_its_candidate,
            dur_outbox__rebuild_restores_every_queued_job_exactly_once]


# ==========================================================================
# Engine
# ==========================================================================
async def _drain(engine, lease, prepared):
    events = []
    async for event in engine.generate(lease, prepared):
        events.append(event)
    return events


def _lease(harness):
    """A lease as `JobStore.claim` mints one, phase instants included (r1 R20)."""
    from ..records import Lease
    now = harness.clock.now()
    return Lease(job_id=harness.ids.uuid(), generation=1, worker_id="worker-a",
                 acquired_at=now, expires_at=harness.clock.at(DEFAULTS.lease_ttl_s),
                 generation_deadline_at=harness.clock.at(DEFAULTS.generation_timeout_s),
                 first_token_deadline_at=harness.clock.at(DEFAULTS.ttft_timeout_s))


def _prepared(harness):
    from ..records import PreparedRequest
    return PreparedRequest(request_id=harness.ids.uuid(), model_revision=b.MODEL,
                           messages=({"role": "user", "content": "Describe this clip."},),
                           max_output_tokens=256, prompt_tokens=1200)


async def api_stream__canonical_events_end_with_authoritative_usage(factory):
    """API-STREAM: the happy path is progress, deltas and one authoritative usage."""
    harness = factory()
    events = await _drain(harness.port, _lease(harness), _prepared(harness))
    assert events[0].type is ChunkEventType.progress
    usage_events = [event for event in events if event.type is ChunkEventType.usage]
    assert len(usage_events) == 1 and usage_events[0].usage is not None
    assert usage_events[0].usage.certainty.value == "authoritative"
    text = "".join(event.payload["content"] for event in events
                   if event.type is ChunkEventType.delta)
    assert text == hook(harness, "text")


async def api_stream__reasoning_delimiters_split_across_chunks(factory):
    """API-STREAM: correct output across arbitrary chunk boundaries: the delimiters
    only exist in the concatenation, never inside one delta."""
    harness = factory(fault="split_reasoning_delimiters")
    events = await _drain(harness.port, _lease(harness), _prepared(harness))
    deltas = [event.payload["content"] for event in events
              if event.type is ChunkEventType.delta]
    joined = "".join(deltas)
    assert joined.count("<think>") == 1 and joined.count("</think>") == 1
    assert not any("<think>" in delta or "</think>" in delta for delta in deltas)
    assert joined.split("</think>")[-1] == "Two people unload boxes."


async def api_stream__a_prefill_stall_produces_no_delta_within_the_budget(factory):
    """API-STREAM: the engine simply stops; W owns the TTFT policy."""
    harness = factory(fault="prefill_stall")
    started = harness.clock.now()
    events = await _drain(harness.port, _lease(harness), _prepared(harness))
    assert [event.type for event in events] == [ChunkEventType.progress]
    assert (harness.clock.now() - started).total_seconds() > DEFAULTS.ttft_timeout_s


async def api_stream__a_midstream_stall_leaves_usage_unknown(factory):
    """API-STREAM: deltas then silence past the inter-event budget, no usage."""
    harness = factory(fault="midstream_stall")
    started = harness.clock.now()
    events = await _drain(harness.port, _lease(harness), _prepared(harness))
    assert any(event.type is ChunkEventType.delta for event in events)
    assert not any(event.type is ChunkEventType.usage for event in events)
    assert (harness.clock.now() - started).total_seconds() > DEFAULTS.tpot_stall_s


async def api_stream__malformed_or_missing_usage_is_never_authoritative(factory):
    """DUR-SETTLE feeds on this: no usage record means the settlement is unknown."""
    for fault in ("malformed_usage", "missing_usage"):
        harness = factory(fault=fault)
        events = await _drain(harness.port, _lease(harness), _prepared(harness))
        assert all(event.usage is None for event in events), fault


async def api_stream__an_abrupt_exit_raises_rather_than_completing(factory):
    """API-STREAM: an engine crash is not a successful terminal event."""
    harness = factory(fault="abrupt_exit")
    try:
        await _drain(harness.port, _lease(harness), _prepared(harness))
    except Exception as exc:
        assert not isinstance(exc, errors.DomainError)   # external process, not a domain error
    else:
        raise AssertionError("an abrupt engine exit looked like a clean stream")


async def api_stream__a_cancellation_race_reports_what_was_produced(factory):
    """API-STREAM: cancel mid-stream yields exactly one usage event for the work
    actually done, and the iterator ends."""
    harness = factory(fault="cancellation_race")
    lease = _lease(harness)
    assert await harness.port.cancel(lease) is True
    events = await _drain(harness.port, lease, _prepared(harness))
    usage_events = [event for event in events if event.type is ChunkEventType.usage]
    assert len(usage_events) == 1
    assert usage_events[0].usage.completion_tokens == 0


async def api_stream__health_and_drain_are_observable(factory):
    """API-STREAM: drain is observable so a rollout can stop taking work."""
    harness = factory()
    assert (await harness.port.health())["ready"] is True
    await harness.port.drain()
    assert (await harness.port.health())["ready"] is False


def engine_cases():
    return [api_stream__canonical_events_end_with_authoritative_usage,
            api_stream__reasoning_delimiters_split_across_chunks,
            api_stream__a_prefill_stall_produces_no_delta_within_the_budget,
            api_stream__a_midstream_stall_leaves_usage_unknown,
            api_stream__malformed_or_missing_usage_is_never_authoritative,
            api_stream__an_abrupt_exit_raises_rather_than_completing,
            api_stream__a_cancellation_race_reports_what_was_produced,
            api_stream__health_and_drain_are_observable]


# ==========================================================================
# TraceSink
# ==========================================================================
async def trace_bounds__an_accepted_offer_is_in_memory_only(factory):
    """TRACE-BOUNDS: offer reports in-memory acceptance and never promises fsync."""
    harness = factory()
    result = await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0,
                                              metadata_bytes=64, harness=harness))
    assert result is TraceOfferResult.accepted_in_memory
    stats = await harness.port.stats()
    assert stats["accepted"] == 1 and stats["in_memory"] == 1
    assert stats["appended"] == 0 and stats["fsynced"] == 0
    # r1 R27: `offer` is metadata-only. Content is charged as it accumulates, through
    # `open`/`add`, so an envelope arriving here with content was never charged and is
    # dropped rather than queued as a capture nobody accounted for.
    smuggled = await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=4_096,
                                                metadata_bytes=16, harness=harness))
    assert smuggled is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["in_memory"] == 1 and stats["in_memory_content_bytes"] == 0
    assert stats["loss_reasons"]["malformed"] == 1


async def trace_bounds__a_content_budget_breach_discards_the_whole_content(factory):
    """TRACE-BOUNDS: no retained partial content pretending to be complete; metadata
    still flows and the loss is counted. The budget is a **running total**: envelopes
    that each fit on their own but not together must not all be kept, so a sink that
    only compares one envelope against the budget fails here."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=4096,
                                              trace_metadata_reserve_bytes=1024))
    budget = hook(harness, "content_budget")()              # 3,072 bytes
    for _ in range(3):
        # 1,024 each: three fit exactly, and each one alone is far inside the budget
        request_id = harness.ids.uuid()
        capture = harness.port.open(request_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
        assert capture.add("c" * 1024) is True
        assert await capture.finish(b.trace(request_id, content_bytes=1024, metadata_bytes=16,
                                           harness=harness)) \
            is TraceOfferResult.accepted_in_memory
    assert (await harness.port.stats())["in_memory_content_bytes"] == budget
    over_id = harness.ids.uuid()
    over = harness.port.open(over_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert over.add("c" * 1024) is False, "the running total was not consulted"
    assert await over.finish(b.trace(over_id, content_bytes=1024, metadata_bytes=16,
                                     harness=harness)) is TraceOfferResult.accepted_in_memory
    kept = hook(harness, "queued")()[-1]
    assert kept.content_bytes == 0 and kept.content_ref is None
    assert kept.content_complete is False
    assert kept.loss_reason is TraceLossReason.memory_budget
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["memory_budget"] == 1
    assert stats["in_memory_content_bytes"] == budget       # never over, never double
    huge_id = harness.ids.uuid()
    huge = harness.port.open(huge_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert huge.add("h" * 99_999) is False
    assert await huge.finish(b.trace(huge_id, content_bytes=99_999, metadata_bytes=16,
                                     harness=harness)) is TraceOfferResult.accepted_in_memory
    assert hook(harness, "queued")()[-1].content_bytes == 0
    assert (await harness.port.stats())["in_memory_content_bytes"] == budget


async def trace_bounds__metadata_exhaustion_drops_with_counters(factory):
    """TRACE-BOUNDS: when the metadata reserve is gone the record is dropped and
    counted; inference is untouched either way."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=8192,
                                              trace_metadata_reserve_bytes=64))
    accepted = await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0,
                                                metadata_bytes=64, harness=harness))
    dropped = await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0,
                                               metadata_bytes=64, harness=harness))
    assert accepted is TraceOfferResult.accepted_in_memory
    assert dropped is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["dropped"] == 1 and stats["loss_reasons"]["metadata_budget"] == 1


async def trace_bounds__a_full_queue_drops_and_inference_continues(factory):
    """TRACE-BOUNDS: the queued-record ceiling drops rather than blocking."""
    harness = factory(limits=DEFAULTS.replace(trace_queue_max=2))
    for _ in range(2):
        assert await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0,
                                                metadata_bytes=8, harness=harness)) \
            is TraceOfferResult.accepted_in_memory
    assert await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0,
                                            metadata_bytes=8, harness=harness)) \
        is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["queue_full"] == 1
    assert stats["accepted"] == 2                            # the sink never blocked


async def trace_bounds__in_memory_appended_and_fsynced_are_separate_states(factory):
    """TRACE-RECOVER depends on this: durability begins only after fsync."""
    harness = factory()
    await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0, harness=harness))
    stats = await harness.port.flush(harness.clock.now())
    assert stats["in_memory"] == 0 and stats["appended"] == 1 and stats["fsynced"] == 0
    harness.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
    await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=0, harness=harness))
    stats = await harness.port.flush(harness.clock.now())
    assert stats["appended"] == 2 and stats["fsynced"] == 2


async def trace_bounds__off_mode_produces_no_trace_at_all(factory):
    """TRACE-BOUNDS / r1 R27: mode off means no row and no content, so off-mode jobs
    stay out of the coverage denominator entirely. The sink drops the envelope and
    counts it `malformed`; it never raises, because the trace path must not be able to
    fail the request path it is called from."""
    harness = factory()
    result = await harness.port.offer(b.trace(harness.ids.uuid(), mode=TraceMode.off,
                                              content_bytes=0, harness=harness))
    assert result is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["accepted"] == 0 and stats["in_memory"] == 0
    assert stats["loss_reasons"]["malformed"] == 1
    # r1 R37: an off-mode (or minimal) request gets a **no-op** capture rather than an
    # exception - the trace path may never raise into the request path, and G needs no
    # branch on the mode. It keeps nothing and charges nothing.
    # r1 R37 + 01 ("minimal stores metadata only"): an off- or minimal-mode request gets
    # a **no-op** capture rather than an exception, and `finish` on one behaves as
    # `offer`. So G opens a capture, adds, finishes and never branches on the mode: a
    # minimal request still produces exactly one metadata row with no content, and an
    # off-mode request produces none at all.
    for mode, expected_rows in ((TraceMode.off, 0), (TraceMode.minimal, 1)):
        harness = factory()
        request_id = harness.ids.uuid()
        capture = harness.port.open(request_id, b.ORG_A, mode, harness.clock.at(600))
        assert capture.add("content that must never be kept") is False
        result = await capture.finish(b.trace(request_id, mode=mode, content_bytes=0,
                                             metadata_bytes=64, harness=harness))
        stats = await harness.port.stats()
        assert stats["in_memory"] == expected_rows, (mode, stats)
        assert stats["in_memory_content_bytes"] == 0
        assert stats["accepted"] == expected_rows
        if expected_rows:
            assert result is TraceOfferResult.accepted_in_memory
            kept = hook(harness, "queued")()[-1]
            assert kept.mode is mode and kept.carries_content is False
            assert kept.metadata_bytes == 64
        else:
            assert result is TraceOfferResult.dropped
            assert stats["loss_reasons"]["malformed"] >= 1
        # the capture is still a context manager, and closing it changes nothing
        with capture:
            pass
        assert (await harness.port.stats())["in_memory"] == expected_rows


async def trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope(factory):
    """TRACE-BOUNDS / r1 R37+R12 + 01: what a no-op capture stores is decided by the mode
    it was **opened** with, never by the envelope it is handed.

    Trusting the envelope makes the trace mode a client-supplied field: an `off` request
    would get a row, a `minimal` one would get content, and a `full` capture that never
    accumulated anything would report complete content it never charged. Every refusal is
    dropped and counted; a loss is never silent (02).
    """
    deadline = None                                  # set per phase below

    # (a) a `full` capture opened without a deadline can never be reaped, so it is a
    # no-op - and it finishes as honest metadata with exactly one counted loss, never
    # `loss_reason: none`
    harness = factory()
    request_id = harness.ids.uuid()
    capture = harness.port.open(request_id, b.ORG_A, TraceMode.full, deadline)
    assert capture.add("x" * 5_000) is False
    result = await capture.finish(b.trace(request_id, content_bytes=0, metadata_bytes=32,
                                          harness=harness))
    assert result is TraceOfferResult.accepted_in_memory
    stats = await harness.port.stats()
    assert stats["in_memory"] == 1 and stats["in_memory_content_bytes"] == 0
    queued = hook(harness, "queued")()[-1]
    assert queued.loss_reason is not TraceLossReason.none, "a discarded capture reported no loss"
    assert sum(stats["loss_reasons"].values()) == 1, stats["loss_reasons"]
    # ... and a content-carrying envelope from the same capture is stripped, not stored
    harness = factory()
    request_id = harness.ids.uuid()
    capture = harness.port.open(request_id, b.ORG_A, TraceMode.full, None)
    assert capture.add("x" * 2_000) is False
    await capture.finish(b.trace(request_id, content_bytes=2_000, metadata_bytes=32,
                                 harness=harness))
    queued = hook(harness, "queued")()[-1]
    assert queued.content_bytes == 0 and queued.content_complete is False
    assert queued.content_ref is None
    assert queued.loss_reason is not TraceLossReason.none
    assert (await harness.port.stats())["in_memory_content_bytes"] == 0

    # (b) a `minimal` capture given a `full` envelope with content: refused, not queued
    for opened, envelope_mode, content in ((TraceMode.minimal, TraceMode.full, 2_000),
                                           (TraceMode.minimal, TraceMode.full, 0),
                                           (TraceMode.off, TraceMode.minimal, 0),
                                           (TraceMode.off, TraceMode.full, 1_000),
                                           (TraceMode.minimal, TraceMode.off, 0)):
        harness = factory()
        request_id = harness.ids.uuid()
        capture = harness.port.open(request_id, b.ORG_A, opened, harness.clock.at(600))
        outcome = await capture.finish(b.trace(request_id, mode=envelope_mode,
                                               content_bytes=content, metadata_bytes=32,
                                               harness=harness))
        stats = await harness.port.stats()
        assert outcome is TraceOfferResult.dropped, (opened, envelope_mode, content)
        assert stats["in_memory"] == 0, f"{opened} capture queued a {envelope_mode} envelope"
        assert stats["in_memory_content_bytes"] == 0
        assert stats["loss_reasons"]["malformed"] == 1, stats["loss_reasons"]

    # (b2) a *full* capture that really accumulated cannot claim more than it charged:
    # those bytes never went through the budget, so the numbers must agree or `add` is
    # decoration and the accounting R27 rests on is fiction
    harness = factory()
    request_id = harness.ids.uuid()
    capture = harness.port.open(request_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert capture.add("y" * 1_000) is True
    inflated = await capture.finish(b.trace(request_id, content_bytes=5_000, metadata_bytes=32,
                                            harness=harness))
    stats = await harness.port.stats()
    assert inflated is TraceOfferResult.dropped, "an envelope claimed content it never charged"
    assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
    assert stats["loss_reasons"]["malformed"] == 1
    # claiming exactly what was charged is the honest case
    harness = factory()
    request_id = harness.ids.uuid()
    capture = harness.port.open(request_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert capture.add("y" * 1_000) is True
    assert await capture.finish(b.trace(request_id, content_bytes=1_000, metadata_bytes=32,
                                        harness=harness)) is TraceOfferResult.accepted_in_memory
    assert (await harness.port.stats())["in_memory_content_bytes"] == 1_000

    # (c) the honest minimal case still works: opened minimal, finished minimal, one row
    harness = factory()
    request_id = harness.ids.uuid()
    capture = harness.port.open(request_id, b.ORG_A, TraceMode.minimal, harness.clock.at(600))
    assert await capture.finish(b.trace(request_id, mode=TraceMode.minimal, content_bytes=0,
                                        metadata_bytes=32, harness=harness)) \
        is TraceOfferResult.accepted_in_memory
    assert (await harness.port.stats())["in_memory"] == 1
    assert hook(harness, "queued")()[-1].carries_content is False

    # (d) identity is checked on the no-op path too, exactly as on the accumulating one
    harness = factory()
    request_id = harness.ids.uuid()
    capture = harness.port.open(request_id, b.ORG_A, TraceMode.minimal, harness.clock.at(600))
    foreign = await capture.finish(b.trace(request_id, org_id=b.ORG_B, mode=TraceMode.minimal,
                                           content_bytes=0, metadata_bytes=32, harness=harness))
    stats = await harness.port.stats()
    assert foreign is TraceOfferResult.dropped, "a no-op capture filed another tenant's row"
    assert stats["in_memory"] == 0 and stats["loss_reasons"]["malformed"] == 1
    other = harness.port.open(request_id, b.ORG_A, TraceMode.minimal, harness.clock.at(600))
    assert await other.finish(b.trace(harness.ids.uuid(), mode=TraceMode.minimal,
                                      content_bytes=0, metadata_bytes=32, harness=harness)) \
        is TraceOfferResult.dropped
    assert (await harness.port.stats())["in_memory"] == 0


async def trace_bounds__concurrent_captures_share_one_budget(factory):
    """TRACE-BOUNDS / r1 R27: bytes are charged while content accumulates, so the
    budget is shared across every open capture. Two requests that each fit alone but
    not together must not both be kept: whichever crosses the line loses its whole
    content capture, the others are untouched, and the loss is counted."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=5_000,
                                              trace_metadata_reserve_bytes=1_000))
    budget = hook(harness, "content_budget")()           # 4,000 bytes of content
    first_id, second_id = harness.ids.uuid(), harness.ids.uuid()
    first = harness.port.open(first_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    second = harness.port.open(second_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    part = "x" * 1_000
    for _ in range(3):
        assert first.add(part) is True
    assert (await harness.port.stats())["in_memory_content_bytes"] == 3_000
    # the second capture fits on its own, but not beside the first
    assert second.add(part) is True                      # 4,000 exactly
    assert second.add(part) is False, "the shared budget was overrun"
    assert second.add("x") is False                      # and it stays over
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["memory_budget"] == 1
    assert stats["in_memory_content_bytes"] == 3_000     # the loser released its bytes
    # one loss count per capture, whatever sequence ends it: a third capture that
    # breaches and is then abandoned contributes exactly one loss, not two, or the loss
    # metrics a capacity decision reads are inflated by an ordinary `finally`
    breached = (await harness.port.stats())["loss_reasons"]["memory_budget"]
    third = harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full,
                              harness.clock.at(600))
    assert third.add(part + "x") is False            # one byte more than is left
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["memory_budget"] == breached + 1
    await third.abandon(TraceLossReason.abandoned)
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["memory_budget"] == breached + 1, stats["loss_reasons"]
    assert stats["loss_reasons"].get("abandoned", 0) == 0, \
        "an already-counted capture was counted a second time"
    # the loser finishes as honest metadata; the winner keeps its content
    lossy = await second.finish(b.trace(second_id, content_bytes=1_000, metadata_bytes=16,
                                        harness=harness))
    kept = await first.finish(b.trace(first_id, content_bytes=3_000, metadata_bytes=16,
                                      harness=harness))
    assert lossy is TraceOfferResult.accepted_in_memory
    assert kept is TraceOfferResult.accepted_in_memory
    queued = {env.request_id: env for env in hook(harness, "queued")()}
    assert queued[second_id].content_bytes == 0
    assert queued[second_id].content_ref is None
    assert queued[second_id].content_complete is False
    assert queued[second_id].loss_reason is TraceLossReason.memory_budget
    assert queued[first_id].content_bytes == 3_000
    assert queued[first_id].content_complete is True
    # and the accounting never exceeded the budget
    assert (await harness.port.stats())["in_memory_content_bytes"] <= budget


async def trace_bounds__an_abandoned_capture_releases_its_bytes(factory):
    """TRACE-BOUNDS / r1 R27: a request that dies mid-capture must not hold capture
    budget for the rest of the process's life. `abandon` releases the bytes and counts
    the loss, and the freed budget is usable by the next request."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=5_000,
                                              trace_metadata_reserve_bytes=1_000))
    doomed_id = harness.ids.uuid()
    doomed = harness.port.open(doomed_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert doomed.add("y" * 2_000) is True
    assert (await harness.port.stats())["in_memory_content_bytes"] == 2_000
    await doomed.abandon(TraceLossReason.abandoned)
    stats = await harness.port.stats()
    assert stats["in_memory_content_bytes"] == 0
    assert stats["in_memory"] == 0 and stats["accepted"] == 0
    assert stats["loss_reasons"]["abandoned"] == 1
    assert stats["open_captures"] == 0
    # r1 R37: idempotent, and it never raises - releasing twice releases once
    await doomed.abandon(TraceLossReason.abandoned)
    assert (await harness.port.stats())["loss_reasons"]["abandoned"] == 1
    assert (await harness.port.stats())["in_memory_content_bytes"] == 0
    # and a capture contributes **one** loss count however it ends: finishing an already
    # abandoned capture must not count its loss a second time, or the loss metrics a
    # capacity decision is made from are inflated by the ordinary abandon-then-finish
    # sequence a `finally` produces.
    late = await doomed.finish(b.trace(doomed_id, content_bytes=2_000, metadata_bytes=16,
                                       harness=harness))
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["abandoned"] == 1, stats["loss_reasons"]
    assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
    assert late is TraceOfferResult.dropped, "an abandoned capture reported acceptance"
    assert await doomed.finish(b.trace(doomed_id, content_bytes=2_000, metadata_bytes=16,
                                       harness=harness)) is late      # the first answer
    # the whole budget is available again, and a finished capture is not abandonable
    survivor_id = harness.ids.uuid()
    survivor = harness.port.open(survivor_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert survivor.add("z" * 2_000) is True
    await survivor.finish(b.trace(survivor_id, content_bytes=2_000, metadata_bytes=16,
                                  harness=harness))
    assert (await harness.port.stats())["in_memory_content_bytes"] == 2_000
    # r1 R37: finishing twice queues one record, and raises nothing
    assert await survivor.finish(b.trace(survivor_id, content_bytes=2_000, metadata_bytes=16,
                                         harness=harness)) is TraceOfferResult.accepted_in_memory
    stats = await harness.port.stats()
    assert stats["in_memory"] == 1 and stats["in_memory_content_bytes"] == 2_000

    # a capture used as a context manager abandons itself if the request dies first
    with harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full, harness.clock.at(600)) as dying:
        assert dying.add("y" * 500) is True
        assert (await harness.port.stats())["in_memory_content_bytes"] == 2_500
    assert (await harness.port.stats())["in_memory_content_bytes"] == 2_000
    assert (await harness.port.stats())["open_captures"] == 0


async def trace_bounds__a_capture_belongs_to_its_own_request(factory):
    """TRACE-BOUNDS: the envelope handed to `finish` must be this capture's own, or
    one request's content would be filed under another request - and another tenant."""
    harness = factory()
    own_id = harness.ids.uuid()
    capture = harness.port.open(own_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert capture.add("some content") is True
    # r1 R37: refused *and counted*, never raised - a trace bug may not become the
    # request's error. The capture's bytes are released either way.
    foreign = b.trace(harness.ids.uuid(), content_bytes=12, harness=harness)
    assert await capture.finish(foreign) is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
    assert stats["loss_reasons"]["malformed"] >= 1
    # a part that is not content is dropped and counted, never a TypeError into the
    # request path (r1 R37)
    junk = harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full,
                             harness.clock.at(600))
    assert junk.add(b"bytes are fine") is True
    for part in (12_345, None, {"not": "content"}, ["neither"]):
        assert junk.add(part) is False, part
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["malformed"] >= 1
    assert stats["in_memory_content_bytes"] == 0, "a malformed part kept its charge"
    minimal = harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.minimal,
                                harness.clock.at(600))
    assert minimal.add("content") is False
    other_tenant = harness.port.open(own_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert other_tenant.add("some content") is True
    assert await other_tenant.finish(
        b.trace(own_id, org_id=b.ORG_B, content_bytes=12, harness=harness)) \
        is TraceOfferResult.dropped
    assert (await harness.port.stats())["in_memory_content_bytes"] == 0


async def trace_bounds__minimal_mode_never_carries_content(factory):
    """TRACE-BOUNDS / r1 R12: `minimal` stores metadata only (01's privacy rule), so
    a minimal-mode envelope with content is malformed, not a cheap way to capture
    unconsented content the byte budget never sees. Every accepted content byte is
    charged, whatever the mode claims."""
    from ..records import TraceEnvelope
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=20_000,
                                              trace_metadata_reserve_bytes=10_000))
    # the record refuses to exist at all
    try:
        b.trace(harness.ids.uuid(), mode=TraceMode.minimal, content_bytes=5_000_000,
                harness=harness)
    except ValueError:
        pass
    else:
        raise AssertionError("a minimal-mode envelope carried content")
    # and a sink handed one anyway (assembled from raw bytes, not through the model)
    # drops it whole, counted, with nothing queued and no bytes charged
    honest = b.trace(harness.ids.uuid(), mode=TraceMode.minimal, content_bytes=0,
                     metadata_bytes=32, harness=harness)
    smuggled = TraceEnvelope.model_construct(
        **{**honest.model_dump(), "content_bytes": 5_000_000, "content_complete": True,
           "content_ref": f"traces/{b.ORG_A}/smuggled.json.zst"})
    assert await harness.port.offer(smuggled) is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
    assert stats["loss_reasons"].get("malformed") == 1
    # metadata-only minimal capture still flows
    assert await harness.port.offer(honest) is TraceOfferResult.accepted_in_memory
    stats = await harness.port.stats()
    assert stats["in_memory"] == 1 and stats["in_memory_content_bytes"] == 0
    assert hook(harness, "queued")()[-1].content_ref is None


async def trace_bounds__a_flush_leaves_open_captures_alone(factory):
    """TRACE-BOUNDS / r1 R27: flushing the queue must not zero the bytes of captures
    that are still **accumulating**. A sink that reset the counter would let every
    in-flight capture spend the whole budget again, and the process would overrun the
    memory bound it exists to enforce."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=5_000,
                                              trace_metadata_reserve_bytes=1_000))
    budget = hook(harness, "content_budget")()          # 4,000
    open_id = harness.ids.uuid()
    still_open = harness.port.open(open_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert still_open.add("o" * 3_000) is True
    finished_id = harness.ids.uuid()
    finished = harness.port.open(finished_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert finished.add("f" * 1_000) is True
    await finished.finish(b.trace(finished_id, content_bytes=1_000, metadata_bytes=16,
                                  harness=harness))
    assert (await harness.port.stats())["in_memory_content_bytes"] == budget

    stats = await harness.port.flush(harness.clock.now())
    assert stats["appended"] == 1 and stats["in_memory"] == 0
    assert stats["in_memory_content_bytes"] == 3_000, \
        "the flush released bytes a capture is still accumulating"
    assert stats["open_captures"] == 1
    # so the budget is still honoured for the next request
    late = harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert late.add("l" * 1_000) is True             # exactly fills it
    assert (await harness.port.stats())["in_memory_content_bytes"] == budget
    assert late.add("x") is False                    # and one byte more is a breach,
    # which discards that capture's whole content, releasing its 1,000 bytes
    assert (await harness.port.stats())["in_memory_content_bytes"] == 3_000
    await still_open.abandon(TraceLossReason.abandoned)
    assert (await harness.port.stats())["in_memory_content_bytes"] == 0


async def trace_bounds__a_dropped_finish_releases_its_charge(factory):
    """TRACE-BOUNDS: a capture whose `finish` is dropped - the queue is full, or the
    metadata reserve is gone - must release the bytes it had charged. Keeping them
    would leak the whole budget away one dropped record at a time, and the sink would
    stop capturing anything with nothing queued to show for it."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=9_000,
                                              trace_metadata_reserve_bytes=1_000,
                                              trace_queue_max=1))
    first_id = harness.ids.uuid()
    first = harness.port.open(first_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert first.add("a" * 1_000) is True
    await first.finish(b.trace(first_id, content_bytes=1_000, metadata_bytes=16,
                               harness=harness))
    assert (await harness.port.stats())["in_memory"] == 1        # the queue is now full
    dropped_id = harness.ids.uuid()
    dropped = harness.port.open(dropped_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert dropped.add("b" * 2_000) is True
    assert (await harness.port.stats())["in_memory_content_bytes"] == 3_000
    result = await dropped.finish(b.trace(dropped_id, content_bytes=2_000, metadata_bytes=16,
                                          harness=harness))
    assert result is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["queue_full"] == 1
    assert stats["in_memory_content_bytes"] == 1_000, \
        "a dropped record kept its charge on the budget"
    assert stats["in_memory"] == 1

    # ... and the same for the *other* drop reason: a finish refused because the metadata
    # reserve is exhausted must release its charge too. The corpus mutant that keeps it
    # (m20b) dies here rather than only on the queue_full path.
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=9_000,
                                              trace_metadata_reserve_bytes=64,
                                              trace_queue_max=1_000))
    filler_id = harness.ids.uuid()
    assert await harness.port.offer(b.trace(filler_id, content_bytes=0, metadata_bytes=64,
                                            harness=harness)) \
        is TraceOfferResult.accepted_in_memory          # the reserve is now exhausted
    starved_id = harness.ids.uuid()
    starved = harness.port.open(starved_id, b.ORG_A, TraceMode.full, harness.clock.at(600))
    assert starved.add("m" * 1_000) is True
    assert (await harness.port.stats())["in_memory_content_bytes"] == 1_000
    refused = await starved.finish(b.trace(starved_id, content_bytes=1_000, metadata_bytes=64,
                                           harness=harness))
    stats = await harness.port.stats()
    assert refused is TraceOfferResult.dropped
    assert stats["loss_reasons"]["metadata_budget"] == 1, stats["loss_reasons"]
    assert stats["in_memory_content_bytes"] == 0, \
        "a metadata-budget drop kept its content charge"
    assert stats["in_memory"] == 1                      # only the filler


async def trace_bounds__an_open_capture_past_its_deadline_is_reaped(factory):
    """TRACE-BOUNDS / r1 R37: a request that dies without its `finally` running - a
    killed process, a connection lost mid-stream - would hold capture budget until the
    process restarted. The sink reaps captures still open past the job's `deadline_at`
    plus a grace period, releases their bytes and counts them `abandoned`."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=3_000,
                                              trace_metadata_reserve_bytes=1_000))
    reap = hook(harness, "reap")
    deadline = harness.clock.at(100)
    lost = harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full, deadline)
    assert lost.add("z" * 1_900) is True
    assert (await harness.port.stats())["in_memory_content_bytes"] == 1_900
    assert reap() == 0                              # inside its deadline: left alone
    assert reap(-100.0) == 0, "a negative grace period reaped a live capture"
    assert (await harness.port.stats())["in_memory_content_bytes"] == 1_900
    # r1 R37: a capture opened without a deadline could never be reaped, so it is a no-op
    # instead of a leak - it holds no bytes at all
    with harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full, None) as undeadlined:
        # 50 bytes would fit the budget easily: only the missing deadline makes this a
        # no-op capture, so a store that ignored the deadline would charge them
        assert undeadlined.add("z" * 50) is False
        assert (await harness.port.stats())["in_memory_content_bytes"] == 1_900
    assert (await harness.port.stats())["in_memory_content_bytes"] == 1_900
    harness.clock.advance(120)
    assert reap() == 0, "reaped before the grace period"
    harness.clock.advance(60)
    assert reap() == 1
    stats = await harness.port.stats()
    assert stats["in_memory_content_bytes"] == 0 and stats["open_captures"] == 0
    assert stats["loss_reasons"]["abandoned"] == 1
    assert reap() == 0                              # idempotent
    # the freed budget is usable, and the reaped capture keeps nothing
    survivor = harness.port.open(harness.ids.uuid(), b.ORG_A, TraceMode.full, deadline)
    assert survivor.add("w" * 1_900) is True
    assert lost.add("more") is False
    # a crash while captures are open must not drive the counters negative
    crash = hook(harness, "crash")
    crash()
    await survivor.abandon(TraceLossReason.abandoned)
    assert (await harness.port.stats())["in_memory_content_bytes"] == 0


def tracesink_cases():
    return [trace_bounds__an_accepted_offer_is_in_memory_only,
            trace_bounds__minimal_mode_never_carries_content,
            trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope,
            trace_bounds__concurrent_captures_share_one_budget,
            trace_bounds__an_abandoned_capture_releases_its_bytes,
            trace_bounds__a_capture_belongs_to_its_own_request,
            trace_bounds__a_flush_leaves_open_captures_alone,
            trace_bounds__a_dropped_finish_releases_its_charge,
            trace_bounds__an_open_capture_past_its_deadline_is_reaped,
            trace_bounds__a_content_budget_breach_discards_the_whole_content,
            trace_bounds__metadata_exhaustion_drops_with_counters,
            trace_bounds__a_full_queue_drops_and_inference_continues,
            trace_bounds__in_memory_appended_and_fsynced_are_separate_states,
            trace_bounds__off_mode_produces_no_trace_at_all]


# ==========================================================================
# FeedbackService
# ==========================================================================
async def _owned_request(harness):
    from dataclasses import replace
    from .jobs import _admit
    jobs = hook(harness, "jobs")
    request, admission = await _admit(replace(harness, port=jobs))
    return request, admission


async def feedback_ack__acceptance_is_durable_and_provenance_is_server_set(factory):
    """FEEDBACK-ACK: ownership from the durable job, channel and role stamped by
    the server, and an outbox event before the acknowledgment."""
    harness = factory()
    request, _ = await _owned_request(harness)
    idem = b.idem(request, "fb-1", operation="feedback")
    record = await harness.port.accept(
        b.auth(), request.request_id,
        b.feedback(FeedbackName.correction, "two people unloading a van",
                   comment="the robot is a hand truck"), idem)
    assert record.channel is FeedbackChannel.api and record.author_role is AuthorRole.customer
    assert record.feedback_id.startswith("fb_") and record.org_id == b.ORG_A
    assert record.name is FeedbackName.correction and record.calibration_set is None
    assert hook(harness, "outbox")()                       # projection queued, not awaited
    assert await harness.port.list_owned(b.auth(), request.request_id) == (record,)


async def feedback_ack__the_body_is_one_valid_signal_with_a_required_key(factory):
    """FEEDBACK-ACK / r1 R3: the name fixes the value's type (`research/traces/06`
    §2), an empty body is a 400 rather than a row that records nothing, and the
    idempotency key is required so a retry can never become a second row."""
    harness = factory()
    request, _ = await _owned_request(harness)
    bad_bodies = (
        {},                                                  # nothing at all
        {"comment": "just a comment"},                        # no name, no value
        b.feedback(FeedbackName.thumb, 3),                    # thumb is boolean
        b.feedback(FeedbackName.rating, 7),                   # rating is 1..5
        b.feedback(FeedbackName.rating, 0),
        b.feedback(FeedbackName.rating, True),                # a boolean is not a rating
        b.feedback(FeedbackName.rating, 1.0),                 # nor is a JSON float
        b.feedback(FeedbackName.correction, "   "),           # nonempty text
        b.feedback(FeedbackName.comment, False),
        {"name": "sentiment", "value": "good"},               # not a known signal
        [{"name": "thumb", "value": True}],                   # not even an object
    )
    for n, body in enumerate(bad_bodies):
        try:
            await harness.port.accept(b.auth(), request.request_id, body,
                                      b.idem(request, f"fb-bad-{n}", operation="feedback"))
        except errors.InvalidRequest as exc:
            assert errors.http_status(exc.code) == 400
        else:
            raise AssertionError(f"an invalid feedback body was accepted: {body}")
    keyless = b.idem(request, None, operation="feedback")
    try:
        await harness.port.accept(b.auth(), request.request_id,
                                  b.feedback(FeedbackName.thumb, True), keyless)
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("feedback was accepted without an idempotency key")
    assert await harness.port.list_owned(b.auth(), request.request_id) == ()
    for name, value in ((FeedbackName.thumb, True), (FeedbackName.rating, 5),
                        (FeedbackName.comment, "clear enough")):
        record = await harness.port.accept(b.auth(), request.request_id,
                                           b.feedback(name, value),
                                           b.idem(request, f"fb-{name}", operation="feedback"))
        assert record.name is name and record.value == value


async def feedback_ack__ownership_does_not_wait_for_the_projection(factory):
    """FEEDBACK-ACK: submitted before any trace row exists; cross-tenant is a 404."""
    harness = factory()
    request, _ = await _owned_request(harness)
    await harness.port.accept(b.auth(), request.request_id, b.feedback(),
                              b.idem(request, "fb-1", operation="feedback"))
    other = b.auth(org_id=b.ORG_B, key_id=b.KEY_B)
    for call in (harness.port.accept(other, request.request_id,
                                     b.feedback(FeedbackName.thumb, False),
                                     b.idem(request, "fb-x", operation="feedback")
                                     .model_copy(update={"org_id": b.ORG_B})),
                 harness.port.list_owned(other, request.request_id)):
        try:
            await call
        except errors.NotFound:
            pass
        else:
            raise AssertionError("another org reached the feedback")
    try:
        await harness.port.accept(b.auth(), harness.ids.uuid(), b.feedback(),
                                 b.idem(request, "fb-unknown", operation="feedback"))
    except errors.NotFound:
        pass
    else:
        raise AssertionError("feedback was accepted for an unknown request")
    # r1 R10: the caller's own org, but another org's idempotency scope
    foreign_scope = b.idem(request, "fb-scope", operation="feedback") \
        .model_copy(update={"org_id": b.ORG_B})
    try:
        await harness.port.accept(b.auth(), request.request_id, b.feedback(), foreign_scope)
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError("feedback used another org's idempotency scope")


async def feedback_ack__replay_is_idempotent_and_a_changed_payload_conflicts(factory):
    """FEEDBACK-ACK: a replayed submission survives exactly once; the same key with
    a different payload is a conflict, not a second row."""
    harness = factory()
    request, _ = await _owned_request(harness)
    idem = b.idem(request, "fb-1", operation="feedback")
    first = await harness.port.accept(b.auth(), request.request_id, b.feedback(), idem)
    again = await harness.port.accept(b.auth(), request.request_id, b.feedback(), idem)
    assert again == first
    try:
        await harness.port.accept(b.auth(), request.request_id,
                                  b.feedback(FeedbackName.rating, 1),
                                  b.idem(request, "fb-1", operation="feedback",
                                         payload="changed"))
    except errors.IdempotencyConflict:
        pass
    else:
        raise AssertionError("a changed feedback payload reused its key")
    assert len(await harness.port.list_owned(b.auth(), request.request_id)) == 1


async def feedback_ack__a_client_cannot_forge_provenance(factory):
    """FEEDBACK-ACK: no spoofed author role, channel or calibration label."""
    harness = factory()
    request, _ = await _owned_request(harness)
    for field in ("author_role", "channel", "org_id", "feedback_id", "created_at",
                  "author_principal"):
        body = {**b.feedback(), field: "operator"}
        try:
            await harness.port.accept(b.auth(), request.request_id, body,
                                      b.idem(request, "fb-forge", operation="feedback"))
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"a client set {field}")
    # r1 R31: `calibration_set` is server-set and exists only through the operator
    # path, so even an operator session cannot send it on `accept`
    for auth in (b.auth(), b.auth(role=Role.operator)):
        try:
            await harness.port.accept(auth, request.request_id,
                                      {**b.feedback(), "calibration_set": "golden"},
                                      b.idem(request, "fb-cal", operation="feedback"))
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError("a client stamped calibration membership")
    # and an operator session on the customer path is still a customer signal
    operator_says = await harness.port.accept(b.auth(role=Role.operator), request.request_id,
                                              b.feedback(FeedbackName.thumb, True),
                                              b.idem(request, "fb-op-customer",
                                                     operation="feedback"))
    assert operator_says.author_role is AuthorRole.customer
    assert operator_says.calibration_set is None


async def feedback_ack__an_operator_may_label_a_calibration_set(factory):
    """FEEDBACK-ACK / r1 R31+R19+R26: operator provenance exists only through
    `label_calibration`. It is operator-only, idempotent, audited, and platform-wide:
    the organization comes from the labelled row, not from the operator's session."""
    harness = factory()
    request, _ = await _owned_request(harness)
    idem = b.idem(request, "cal-1", operation="calibration.label")
    try:
        await harness.port.label_calibration(b.auth(), request.request_id, "golden", idem)
    except errors.Forbidden:
        pass
    else:
        raise AssertionError("a customer labelled a calibration set")
    record = await harness.port.label_calibration(b.auth(role=Role.operator),
                                                  request.request_id, "golden", idem)
    assert record.calibration_set == "golden" and record.author_role is AuthorRole.operator
    assert record.org_id == request.org_id          # the row's tenant, not the operator's
    assert await harness.port.label_calibration(b.auth(role=Role.operator), request.request_id,
                                                "golden", idem) == record      # idempotent
    for bad_label, bad_idem in (("", idem), ("   ", idem),
                                ("golden", b.idem(request, None,
                                                  operation="calibration.label"))):
        try:
            await harness.port.label_calibration(b.auth(role=Role.operator), request.request_id,
                                                 bad_label, bad_idem)
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"label {bad_label!r} with key {bad_idem.key!r} was accepted")
    # r1 R26: platform-wide. An operator whose own session names another organization
    # still labels this row, and the label belongs to the *row's* tenant - a store
    # using `auth.org_id` would file it under the operator's org, where the customer
    # who owns the trace could never see it and the calibration set would be split.
    foreign_operator = b.auth(org_id=b.ORG_B, key_id=b.KEY_B, role=Role.operator)
    cross = await harness.port.label_calibration(
        foreign_operator, request.request_id, "golden",
        b.idem(request, "cal-cross", operation="calibration.label"))
    assert cross.org_id == request.org_id, "the label was filed under the operator's org"
    assert cross.author_role is AuthorRole.operator and cross.calibration_set == "golden"
    entries = [entry for entry in hook(harness, "audit")()
               if entry.get("event") == "label_calibration"]
    assert len(entries) == 2 and {entry["label"] for entry in entries} == {"golden"}
    assert entries[0]["operator"] == b.auth(role=Role.operator).principal
    assert all(entry["org_id"] == request.org_id for entry in entries)


def feedback_cases():
    return [feedback_ack__acceptance_is_durable_and_provenance_is_server_set,
            feedback_ack__the_body_is_one_valid_signal_with_a_required_key,
            feedback_ack__ownership_does_not_wait_for_the_projection,
            feedback_ack__replay_is_idempotent_and_a_changed_payload_conflicts,
            feedback_ack__a_client_cannot_forge_provenance,
            feedback_ack__an_operator_may_label_a_calibration_set]


# ==========================================================================
# JudgeCoordinator
# ==========================================================================
def _run(harness, org_id=b.ORG_A):
    from ..records import JudgeRun
    return JudgeRun(run_id=harness.ids.uuid(), org_id=org_id, sample_ids=(harness.ids.uuid(),),
                    consent=b.consent(org_id), rubric_version="rubric_v1",
                    model_revision="claude-opus-5", state=JudgeRunState.dry_run,
                    created_at=harness.clock.now())


async def judge_budget__the_default_is_dry_run_with_no_authorization(factory):
    """JUDGE-BUDGET: default mode dry run, default live budget zero. A pricing
    estimate cannot authorize a submission."""
    harness = factory()
    reserved = await harness.port.reserve(_run(harness), b.consent(), Decimal("1.00"))
    assert reserved.state is JudgeRunState.dry_run and reserved.reserved_cost == 0
    try:
        await harness.port.begin_submit(reserved.run_id)
    except errors.BudgetExceeded:
        pass
    else:
        raise AssertionError("a dry run authorized a live submission")


async def judge_budget__reservations_include_outstanding_and_ambiguous_runs(factory):
    """JUDGE-BUDGET: the hard cap counts every run whose money is not yet free."""
    harness = factory(judge_mode="live", budget="1.00")
    first = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.60"))
    assert first.state is JudgeRunState.reserved
    try:
        await harness.port.reserve(_run(harness), b.consent(), Decimal("0.50"))
    except errors.BudgetExceeded:
        pass
    else:
        raise AssertionError("outstanding reservations were not counted")
    await harness.port.begin_submit(first.run_id)
    # No provider id: the submission may or may not have reached the provider, which
    # is exactly when the money must keep counting against the budget.
    ambiguous = await harness.port.quarantine(first.run_id, "submission timeout")
    assert ambiguous.state is JudgeRunState.ambiguous
    assert ambiguous.reserved_cost == Decimal("0.60")
    available = hook(harness, "available")
    assert available() == Decimal("0.40")
    try:
        await harness.port.reserve(_run(harness), b.consent(), Decimal("0.50"))
    except errors.BudgetExceeded:
        pass
    else:
        raise AssertionError("an ambiguous run freed its reservation")
    # the same after a provider id is known: still outstanding until it settles
    with_id = await harness.port.resolve_ambiguous(
        first.run_id, b.auth(role=Role.operator), JudgeResolution.adopt_provider_evidence,
        "found in the provider console", external_id="batch_placeholder_1")
    assert with_id.state is JudgeRunState.collecting and with_id.reserved_cost == Decimal("0.60")
    assert available() == Decimal("0.40")
    try:
        await harness.port.reserve(_run(harness), b.consent(), Decimal("0.50"))
    except errors.BudgetExceeded:
        pass
    else:
        raise AssertionError("a collecting run freed its reservation")


async def judge_budget__one_submission_intent_per_run(factory):
    """JUDGE-BUDGET: duplicate calls produce one run and one provider batch."""
    harness = factory(judge_mode="live", budget="1.00")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.10"))
    first = await harness.port.begin_submit(run.run_id)
    again = await harness.port.begin_submit(run.run_id)
    assert first.submit_intent is not None and again.submit_intent == first.submit_intent
    submitted = await harness.port.record_submission(run.run_id, "batch_placeholder_1")
    assert submitted.state is JudgeRunState.submitted
    assert await harness.port.record_submission(run.run_id, "batch_placeholder_1") == submitted
    try:
        await harness.port.record_submission(run.run_id, "batch_placeholder_2")
    except errors.AmbiguousSubmission:
        pass
    else:
        raise AssertionError("a run recorded two provider batches")
    # and an empty provider id is the very ambiguity this call exists to remove
    other = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.10"))
    await harness.port.begin_submit(other.run_id)
    for empty in ("", "   ", None):
        try:
            await harness.port.record_submission(other.run_id, empty)
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"provider batch id {empty!r} was recorded")
    assert hook(harness, "runs")()[other.run_id].external_batch_id is None


async def judge_budget__reserving_a_run_twice_does_not_reset_it(factory):
    """JUDGE-BUDGET: duplicate calls produce one run and one intent (01). A second
    reserve must not revive an ambiguous run, mint a second submission intent or
    reserve the budget twice."""
    harness = factory(judge_mode="live", budget="10.00")
    run = _run(harness)
    reserved = await harness.port.reserve(run, b.consent(), Decimal("4.00"))
    assert await harness.port.reserve(run, b.consent(), Decimal("4.00")) == reserved
    available = hook(harness, "available")
    assert available() == Decimal("6.00")              # one reservation, not two
    first = await harness.port.begin_submit(run.run_id)
    await harness.port.quarantine(run.run_id, "submission timeout")
    again = await harness.port.reserve(run, b.consent(), Decimal("4.00"))
    assert again.state in {JudgeRunState.ambiguous, JudgeRunState.quarantined}
    assert again.submit_intent == first.submit_intent
    try:
        await harness.port.begin_submit(run.run_id)
    except errors.AmbiguousSubmission:
        pass
    else:
        raise AssertionError("re-reserving an ambiguous run authorized a second submission")


async def judge_budget__an_ambiguous_run_never_resubmits(factory):
    """JUDGE-BUDGET: resolve with provider evidence, never a second billable batch."""
    harness = factory(judge_mode="live", budget="1.00")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.10"))
    await harness.port.begin_submit(run.run_id)
    await harness.port.quarantine(run.run_id, "no provider id before the timeout")
    try:
        await harness.port.begin_submit(run.run_id)
    except errors.AmbiguousSubmission:
        pass
    else:
        raise AssertionError("an ambiguous run was resubmitted")


async def judge_budget__settlement_cannot_exceed_the_reservation(factory):
    """JUDGE-BUDGET: the reservation is a hard maximum; settling frees the rest."""
    harness = factory(judge_mode="live", budget="1.00")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.40"))
    await harness.port.begin_submit(run.run_id)
    await harness.port.record_submission(run.run_id, "batch_placeholder_1")
    try:
        await harness.port.settle(run.run_id, Decimal("0.90"))
    except errors.BudgetExceeded:
        pass
    else:
        raise AssertionError("actual cost exceeded the reservation")
    settled = await harness.port.settle(run.run_id, Decimal("0.25"))
    assert settled.state is JudgeRunState.settled and settled.actual_cost == Decimal("0.25")
    assert await harness.port.settle(run.run_id, Decimal("0.25")) == settled
    # the unused remainder is free again
    await harness.port.reserve(_run(harness), b.consent(), Decimal("0.70"))


async def judge_budget__revoked_or_missing_consent_is_refused_before_egress(factory):
    """JUDGE-BUDGET / r1 R9: consent is rechecked at submission against the
    **current** consent record, not the snapshot the reservation was taken with, so a
    revocation recorded afterwards blocks egress and releases the reservation. A
    frozen snapshot can never be the whole check: the row a customer revokes is the
    live one, and the snapshot in hand still says `revoked_at: null`."""
    harness = factory(judge_mode="live", budget="1.00")
    revoked = b.consent(revoked_at="2026-09-10T00:00:00Z")
    for consent in (revoked, b.consent(evaluation=False), b.consent(mode=TraceMode.minimal)):
        try:
            await harness.port.reserve(_run(harness), consent, Decimal("0.10"))
        except errors.ConsentMissing:
            pass
        else:
            raise AssertionError("a run without current consent was reserved")
    hook(harness, "revoke_consent")
    harness = factory(judge_mode="live", budget="1.00")   # fresh, with its own hooks
    revoke, available = hook(harness, "revoke_consent"), hook(harness, "available")
    run = _run(harness)
    current = b.consent()                          # nothing revoked at reservation time
    reserved = await harness.port.reserve(run, current, Decimal("0.40"))
    assert reserved.state is JudgeRunState.reserved and reserved.consent.revoked_at is None
    harness.clock.advance(60)
    revoke(b.ORG_A)                                # the customer revokes, in the live row
    try:
        await harness.port.begin_submit(run.run_id)
    except errors.ConsentMissing:
        pass
    else:
        raise AssertionError("consent revoked after the reservation still authorized egress")
    stored = hook(harness, "runs")()[run.run_id]
    assert stored.submit_intent is None, "a revoked run minted a submission intent"
    assert available() == Decimal("1.00"), "the revoked run kept its reservation"
    try:
        await harness.port.begin_submit(run.run_id)   # and it stays refused
    except (errors.ConsentMissing, errors.AmbiguousSubmission, errors.Conflict):
        pass
    else:
        raise AssertionError("a consent-revoked run was submitted on a retry")


async def judge_budget__consent_revoked_while_submitting_holds_the_reservation(factory):
    """JUDGE-BUDGET / r1 R28: release on revocation applies only from `reserved`. Once
    a submission intent exists the batch may already be in flight, so the run becomes
    `ambiguous` with its reservation **held** and is resolved only through
    `resolve_ambiguous`. Freeing the money here would leave a billable provider batch
    running against a budget that looks free."""
    harness = factory(judge_mode="live", budget="1.00")
    revoke = hook(harness, "revoke_consent")
    available, runs = hook(harness, "available"), hook(harness, "runs")
    run = _run(harness)
    await harness.port.reserve(run, b.consent(), Decimal("0.60"))
    submitting = await harness.port.begin_submit(run.run_id)
    assert submitting.state is JudgeRunState.submitting and submitting.submit_intent is not None
    revoke(b.ORG_A)
    try:
        await harness.port.begin_submit(run.run_id)     # the worker retries after a crash
    except errors.ConsentMissing:
        pass
    else:
        raise AssertionError("a revoked run authorized a submission")
    stored = runs()[run.run_id]
    assert stored.state is JudgeRunState.ambiguous, stored.state
    assert stored.reserved_cost == Decimal("0.60"), "the in-flight batch freed its budget"
    assert stored.submit_intent == submitting.submit_intent
    assert available() == Decimal("0.40")
    # it resolves only through R8, and the reservation is freed there
    operator = b.auth(role=Role.operator)
    released = await harness.port.resolve_ambiguous(run.run_id, operator,
                                                    JudgeResolution.release_reservation,
                                                    "provider has no batch for this intent")
    assert released.state is JudgeRunState.quarantined and released.reserved_cost == 0
    assert available() == Decimal("1.00")
    # from `reserved` (no intent yet) revocation still releases at once, per R9
    other = _run(harness)
    hook(harness, "set_consent")(b.consent())
    await harness.port.reserve(other, b.consent(), Decimal("0.50"))
    revoke(b.ORG_A)
    try:
        await harness.port.begin_submit(other.run_id)
    except errors.ConsentMissing:
        pass
    else:
        raise AssertionError("a revoked reservation authorized a submission")
    assert runs()[other.run_id].state is JudgeRunState.cancelled
    assert available() == Decimal("1.00")


async def judge_budget__a_run_and_its_consent_belong_to_one_org(factory):
    """JUDGE-BUDGET / r1 R10: two tenant-bearing arguments must agree. Another
    organization's evaluation consent never authorizes this org's traces leaving the
    platform, whatever the consent row itself says."""
    harness = factory(judge_mode="live", budget="1.00")
    run = _run(harness, org_id=b.ORG_A)
    try:
        await harness.port.reserve(run, b.consent(b.ORG_B), Decimal("0.10"))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError("another org's consent reserved this run")
    assert hook(harness, "runs")() == {}
    # nor is a *stored* run readable by guessing its id from another organization: a
    # run id is not a capability, and the reply would carry its samples and its costs
    reserved = await harness.port.reserve(run, b.consent(b.ORG_A), Decimal("0.10"))
    assert reserved.state is JudgeRunState.reserved
    impostor = reserved.model_copy(update={"org_id": b.ORG_B, "consent": b.consent(b.ORG_B)})
    try:
        stolen = await harness.port.reserve(impostor, b.consent(b.ORG_B), Decimal("0.10"))
    except errors.DomainError as exc:
        assert errors.http_status(exc.code) in (403, 404), exc.code
    else:
        raise AssertionError(f"run {stolen.run_id} of org {stolen.org_id} was read by another org")
    # only ORG_A's own reservation stands; the refused attempts reserved nothing
    assert hook(harness, "available")() == Decimal("0.90")


async def judge_budget__settlement_amounts_are_validated_money(factory):
    """JUDGE-BUDGET / r1 R11: a judge cost is a monetary input. A negative actual
    cost would *raise* the remaining budget above the configured cap and let the next
    reservation spend money that was never granted; non-finite and over-scale amounts
    are refused at the same boundary."""
    harness = factory(judge_mode="live", budget="1.00")
    available = hook(harness, "available")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.40"))
    await harness.port.begin_submit(run.run_id)
    await harness.port.record_submission(run.run_id, "batch_placeholder_1")
    for bad in (Decimal("-100"), Decimal("-0.00000001"), "NaN", "1e5", "0.000000001"):
        try:
            await harness.port.settle(run.run_id, bad)
        except Exception as exc:
            # Typed, and the internal code is checked **before** `http_status`, which
            # raises for internal codes: the previous order made the `or` unreachable and
            # crashed the case for an adapter that legitimately answers `budget_exceeded`.
            assert isinstance(exc, errors.DomainError), f"untyped refusal: {type(exc).__name__}"
            assert exc.code == "budget_exceeded" or errors.http_status(exc.code) in (400, 402), \
                exc.code
        else:
            raise AssertionError(f"an actual cost of {bad!r} settled")
        assert available() <= Decimal("0.60"), "a rejected settlement changed the budget"
    settled = await harness.port.settle(run.run_id, Decimal("0.25"))
    assert settled.state is JudgeRunState.settled
    assert available() == Decimal("0.75")
    # a reservation is the same boundary: negative, non-finite, over-scale and float
    # inputs are all refused, and every refusal is a **typed** domain error - a store
    # that let `money.parse`'s ValueError out would answer 500 to an invalid request
    for bad in (Decimal("-5"), "NaN", "Infinity", "1e5", "0.000000001", 0.5, "-0.00000001"):
        try:
            await harness.port.reserve(_run(harness), b.consent(), bad)
        except Exception as exc:
            assert isinstance(exc, errors.DomainError), \
                f"untyped refusal for {bad!r}: {type(exc).__name__}"
            assert exc.code == "budget_exceeded" or errors.http_status(exc.code) in (400, 402), \
                exc.code
        else:
            raise AssertionError(f"a reservation of {bad!r} was accepted")
        assert available() == Decimal("0.75"), f"{bad!r} moved the budget"


async def judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator(factory):
    """JUDGE-BUDGET / r1 R8: `resolve_ambiguous` is the one way out. An operator
    either adopts the discovered provider batch (collection continues) or releases the
    reservation (terminal `quarantined`); either way there is an audit record, the
    submission intent is unchanged and no second billable batch is ever created."""
    harness = factory(judge_mode="live", budget="1.00")
    available, runs = hook(harness, "available"), hook(harness, "runs")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.40"))
    intent = (await harness.port.begin_submit(run.run_id)).submit_intent
    await harness.port.quarantine(run.run_id, "no provider id before the timeout")
    assert runs()[run.run_id].state is JudgeRunState.ambiguous
    try:
        await harness.port.resolve_ambiguous(run.run_id, b.auth(),
                                             JudgeResolution.release_reservation, "customer ask")
    except errors.Forbidden:
        pass
    else:
        raise AssertionError("a customer resolved an ambiguous run")
    operator = b.auth(role=Role.operator)
    try:
        await harness.port.resolve_ambiguous(run.run_id, operator,
                                             JudgeResolution.adopt_provider_evidence, "found it")
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("provider evidence was adopted without a provider id")
    adopted = await harness.port.resolve_ambiguous(
        run.run_id, operator, JudgeResolution.adopt_provider_evidence,
        "provider console shows batch_placeholder_9", external_id="batch_placeholder_9")
    assert adopted.state is JudgeRunState.collecting
    assert adopted.external_batch_id == "batch_placeholder_9"
    assert adopted.submit_intent == intent          # never a second submission
    assert adopted.reserved_cost == Decimal("0.40")  # still outstanding until settled
    settled = await harness.port.settle(run.run_id, Decimal("0.30"))
    assert settled.state is JudgeRunState.settled and available() == Decimal("0.70")

    # the other resolution: release the reservation, terminally
    other = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.30"))
    await harness.port.begin_submit(other.run_id)
    await harness.port.quarantine(other.run_id, "submission timeout")
    try:
        # r1 R23: the provider id belongs to the adopting resolution only. Releasing a
        # reservation while naming a batch would discard the evidence that it may run.
        await harness.port.resolve_ambiguous(other.run_id, operator,
                                             JudgeResolution.release_reservation,
                                             "provider has no record of it",
                                             external_id="batch_placeholder_7")
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("release_reservation accepted a provider id")
    assert hook(harness, "runs")()[other.run_id].state is JudgeRunState.ambiguous
    released = await harness.port.resolve_ambiguous(other.run_id, operator,
                                                    JudgeResolution.release_reservation,
                                                    "provider has no record of it")
    assert released.state is JudgeRunState.quarantined and released.reserved_cost == 0
    assert available() == Decimal("0.70"), "the released reservation was not freed"
    for call in (harness.port.begin_submit(other.run_id),
                 harness.port.resolve_ambiguous(other.run_id, operator,
                                                JudgeResolution.release_reservation, "again")):
        try:
            await call
        except (errors.AmbiguousSubmission, errors.Conflict):
            pass
        else:
            raise AssertionError("a resolved run was reopened")
    events = [entry["event"] for entry in hook(harness, "audit")()]
    assert events.count("resolve_ambiguous") == 2 and "quarantine" in events


async def judge_budget__an_ambiguous_run_cannot_be_settled_directly(factory):
    """JUDGE-BUDGET / r1 R8: an ambiguous run is resolved by an operator through
    `resolve_ambiguous`, never by settling it. Allowing `settle` here would let an
    automatic collector close a run whose provider batch nobody has confirmed - paying
    for evidence that may not exist, with no audit record and no operator."""
    harness = factory(judge_mode="live", budget="1.00")
    available, runs = hook(harness, "available"), hook(harness, "runs")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.40"))
    await harness.port.begin_submit(run.run_id)
    await harness.port.quarantine(run.run_id, "no provider id before the timeout")
    assert runs()[run.run_id].state is JudgeRunState.ambiguous
    before = available()
    for amount in (Decimal("0.10"), Decimal("0.40"), Decimal("0")):
        try:
            await harness.port.settle(run.run_id, amount)
        except errors.DomainError as exc:
            assert exc.code in ("state_conflict", "budget_exceeded"), exc.code
        else:
            raise AssertionError("an ambiguous run was settled without an operator")
        assert available() == before, "a refused settlement moved the budget"
        assert runs()[run.run_id].state is JudgeRunState.ambiguous
    # the operator path still works, and only then can it settle
    adopted = await harness.port.resolve_ambiguous(
        run.run_id, b.auth(role=Role.operator), JudgeResolution.adopt_provider_evidence,
        "found it in the provider console", external_id="batch_placeholder_5")
    assert adopted.state is JudgeRunState.collecting
    settled = await harness.port.settle(run.run_id, Decimal("0.10"))
    assert settled.state is JudgeRunState.settled and settled.actual_cost == Decimal("0.10")


async def judge_budget__a_settled_run_is_closed(factory):
    """JUDGE-BUDGET: terminal states are sticky. Quarantining a settled run would
    put its money back among the outstanding reservations and erase the settlement
    the audit trail refers to."""
    harness = factory(judge_mode="live", budget="1.00")
    available = hook(harness, "available")
    run = await harness.port.reserve(_run(harness), b.consent(), Decimal("0.40"))
    await harness.port.begin_submit(run.run_id)
    await harness.port.record_submission(run.run_id, "batch_placeholder_1")
    await harness.port.settle(run.run_id, Decimal("0.25"))
    before = available()
    try:
        await harness.port.quarantine(run.run_id, "late provider webhook")
    except errors.Conflict:
        pass
    else:
        raise AssertionError("a settled run was reopened as quarantined")
    assert hook(harness, "runs")()[run.run_id].state is JudgeRunState.settled
    assert available() == before


def judge_cases():
    return [judge_budget__the_default_is_dry_run_with_no_authorization,
            judge_budget__reservations_include_outstanding_and_ambiguous_runs,
            judge_budget__one_submission_intent_per_run,
            judge_budget__reserving_a_run_twice_does_not_reset_it,
            judge_budget__an_ambiguous_run_never_resubmits,
            judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator,
            judge_budget__settlement_cannot_exceed_the_reservation,
            judge_budget__settlement_amounts_are_validated_money,
            judge_budget__an_ambiguous_run_cannot_be_settled_directly,
            judge_budget__a_settled_run_is_closed,
            judge_budget__consent_revoked_while_submitting_holds_the_reservation,
            judge_budget__a_run_and_its_consent_belong_to_one_org,
            judge_budget__revoked_or_missing_consent_is_refused_before_egress]
