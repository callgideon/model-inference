"""Conformance for MediaStore, Scheduler, Engine, TraceSink, FeedbackService and
JudgeCoordinator: MEDIA-SEC/PARITY shapes, DUR-OUTBOX index rules, API-STREAM
engine faults, TRACE-BOUNDS, FEEDBACK-ACK and JUDGE-BUDGET.
"""
from __future__ import annotations

from decimal import Decimal

from .. import errors
from ..limits import DEFAULTS
from ..records import (AuthorRole, ChunkEventType, FeedbackChannel, JudgeRunState, MediaKind,
                       Role, TraceLossReason, TraceMode, TraceOfferResult)
from . import builders as b

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
    harness.extra["put_object"](handle, b"0123456789", "video/mp4")
    ref = await harness.port.finalize_upload(b.ORG_A, handle)
    assert ref.bytes == 10 and ref.digest.startswith("sha256:") and ref.org_id == b.ORG_A
    assert await harness.port.finalize_upload(b.ORG_A, handle) == ref
    assert await harness.port.resolve_owned(b.ORG_A, handle) == ref


async def media_sec__another_org_cannot_resolve_or_finalize(factory):
    """MEDIA-SEC: cross-tenant media access is a 404, never a 403 hint."""
    harness = factory()
    ticket = await harness.port.create_upload(b.ORG_A, {})
    handle = ticket["upload_handle"]
    harness.extra["put_object"](handle, b"bytes", "video/mp4")
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
    harness.extra["put_object"](handle, b"much too large", "video/mp4")
    try:
        await harness.port.finalize_upload(b.ORG_A, handle)
    except errors.RequestTooLarge as exc:
        assert errors.http_status(exc.code) == 413
    else:
        raise AssertionError("an oversize upload was finalized")
    other = await harness.port.create_upload(b.ORG_A, {"accepted_mime": ("video/mp4",)})
    harness.extra["put_object"](other["upload_handle"], b"tiny", "application/zip")
    try:
        await harness.port.finalize_upload(b.ORG_A, other["upload_handle"])
    except errors.UnsupportedMedia:
        pass
    else:
        raise AssertionError("an unsupported media type was finalized")
    try:
        await harness.port.create_upload(b.ORG_A, {"max_bytes": DEFAULTS.max_media_bytes * 2})
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("a caller raised its own byte ceiling")


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
    harness.extra["attach"]("job_stagingfixture", staged_a)
    prepared = await harness.port.prepare("job_stagingfixture", "profile-2")
    assert prepared[0].profile_version == "profile-2"
    assert prepared[0].storage_ref != staged_a[0].storage_ref
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


def mediastore_cases():
    return [media_sec__an_upload_is_owned_verified_and_immutable,
            media_sec__another_org_cannot_resolve_or_finalize,
            media_sec__oversize_and_unsupported_uploads_are_refused,
            media_parity__staging_is_content_addressed_and_tenant_namespaced,
            media_sec__a_foreign_media_reference_is_not_staged]


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


async def dur_outbox__the_index_never_authorizes_execution(factory):
    """DUR-OUTBOX: claiming a candidate mutates no durable job state; only
    JobStore.claim decides the winner."""
    harness = factory()
    jobs = harness.extra.get("jobs")
    if jobs is None:
        return
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
    """DUR-OUTBOX: index visibility is a timeout, not a durable claim."""
    harness = factory()
    await harness.port.enqueue(_index_event(harness))
    first = await harness.port.claim_candidate("worker-a")
    assert first is not None
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
    await harness.port.remove(claimed.job_id)
    assert await harness.port.rebuild(snapshot) == 3
    seen = set()
    while (candidate := await harness.port.claim_candidate("worker-a")) is not None:
        assert candidate.event_id not in seen
        seen.add(candidate.event_id)
    assert seen == {event.event_id for event in snapshot}


def scheduler_cases():
    return [dur_outbox__enqueue_is_replay_safe,
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
    from ..records import Lease
    now = harness.clock.now()
    return Lease(job_id=harness.ids.uuid(), generation=1, worker_id="worker-a",
                 acquired_at=now, expires_at=harness.clock.at(DEFAULTS.lease_ttl_s))


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
    assert text == harness.extra["text"]


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
    result = await harness.port.offer(b.trace(harness.ids.uuid(), harness=harness))
    assert result is TraceOfferResult.accepted_in_memory
    stats = await harness.port.stats()
    assert stats["accepted"] == 1 and stats["in_memory"] == 1
    assert stats["appended"] == 0 and stats["fsynced"] == 0


async def trace_bounds__a_content_budget_breach_discards_the_whole_content(factory):
    """TRACE-BOUNDS: no retained partial content pretending to be complete;
    metadata still flows and the loss is counted."""
    harness = factory(limits=DEFAULTS.replace(trace_capture_bytes=4096,
                                              trace_metadata_reserve_bytes=1024))
    small = await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=1024,
                                             metadata_bytes=16, harness=harness))
    assert small is TraceOfferResult.accepted_in_memory
    big = await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=99_999,
                                           metadata_bytes=16, harness=harness))
    assert big is TraceOfferResult.accepted_in_memory        # metadata survived
    kept = harness.extra["queued"]()[-1]
    assert kept.content_bytes == 0 and kept.content_ref is None
    assert kept.content_complete is False
    assert kept.loss_reason is TraceLossReason.memory_budget
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["memory_budget"] == 1


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
        assert await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=8,
                                                metadata_bytes=8, harness=harness)) \
            is TraceOfferResult.accepted_in_memory
    assert await harness.port.offer(b.trace(harness.ids.uuid(), content_bytes=8,
                                            metadata_bytes=8, harness=harness)) \
        is TraceOfferResult.dropped
    stats = await harness.port.stats()
    assert stats["loss_reasons"]["queue_full"] == 1
    assert stats["accepted"] == 2                            # the sink never blocked


async def trace_bounds__in_memory_appended_and_fsynced_are_separate_states(factory):
    """TRACE-RECOVER depends on this: durability begins only after fsync."""
    harness = factory()
    await harness.port.offer(b.trace(harness.ids.uuid(), harness=harness))
    stats = await harness.port.flush(harness.clock.now())
    assert stats["in_memory"] == 0 and stats["appended"] == 1 and stats["fsynced"] == 0
    harness.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
    await harness.port.offer(b.trace(harness.ids.uuid(), harness=harness))
    stats = await harness.port.flush(harness.clock.now())
    assert stats["appended"] == 2 and stats["fsynced"] == 2


async def trace_bounds__off_mode_produces_no_trace_at_all(factory):
    """TRACE-BOUNDS: mode off means no row and no content, so off-mode jobs stay
    out of the coverage denominator entirely."""
    harness = factory()
    try:
        await harness.port.offer(b.trace(harness.ids.uuid(), mode=TraceMode.off, content_bytes=0,
                                         harness=harness))
    except ValueError:
        pass
    else:
        raise AssertionError("an off-mode request was captured")
    assert (await harness.port.stats())["accepted"] == 0


def tracesink_cases():
    return [trace_bounds__an_accepted_offer_is_in_memory_only,
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
    jobs = harness.extra["jobs"]
    request, admission = await _admit(replace(harness, port=jobs))
    return request, admission


async def feedback_ack__acceptance_is_durable_and_provenance_is_server_set(factory):
    """FEEDBACK-ACK: ownership from the durable job, channel and role stamped by
    the server, and an outbox event before the acknowledgment."""
    harness = factory()
    request, _ = await _owned_request(harness)
    idem = b.idem(request, "fb-1", operation="feedback")
    record = await harness.port.accept(b.auth(), request.request_id,
                                      {"rating": 1, "correction": "closer to two people"}, idem)
    assert record.channel is FeedbackChannel.api and record.author_role is AuthorRole.customer
    assert record.feedback_id.startswith("fb_") and record.org_id == b.ORG_A
    assert record.calibration_set is None
    assert harness.extra["outbox"]()                       # projection queued, not awaited
    assert await harness.port.list_owned(b.auth(), request.request_id) == (record,)


async def feedback_ack__ownership_does_not_wait_for_the_projection(factory):
    """FEEDBACK-ACK: submitted before any trace row exists; cross-tenant is a 404."""
    harness = factory()
    request, _ = await _owned_request(harness)
    await harness.port.accept(b.auth(), request.request_id, {"rating": 1},
                              b.idem(request, "fb-1", operation="feedback"))
    other = b.auth(org_id=b.ORG_B, key_id=b.KEY_B)
    for call in (harness.port.accept(other, request.request_id, {"rating": -1},
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
        await harness.port.accept(b.auth(), harness.ids.uuid(), {"rating": 1},
                                 b.idem(request, "fb-unknown", operation="feedback"))
    except errors.NotFound:
        pass
    else:
        raise AssertionError("feedback was accepted for an unknown request")


async def feedback_ack__replay_is_idempotent_and_a_changed_payload_conflicts(factory):
    """FEEDBACK-ACK: a replayed submission survives exactly once; the same key with
    a different payload is a conflict, not a second row."""
    harness = factory()
    request, _ = await _owned_request(harness)
    idem = b.idem(request, "fb-1", operation="feedback")
    first = await harness.port.accept(b.auth(), request.request_id, {"rating": 1}, idem)
    again = await harness.port.accept(b.auth(), request.request_id, {"rating": 1}, idem)
    assert again == first
    try:
        await harness.port.accept(b.auth(), request.request_id, {"rating": -1},
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
    for body in ({"rating": 1, "author_role": "operator"}, {"rating": 1, "channel": "console"}):
        try:
            await harness.port.accept(b.auth(), request.request_id, body,
                                      b.idem(request, "fb-forge", operation="feedback"))
        except errors.InvalidRequest:
            pass
        else:
            raise AssertionError(f"a client set {sorted(body)[0]}")
    try:
        await harness.port.accept(b.auth(), request.request_id,
                                  {"rating": 1, "calibration_set": "golden"},
                                  b.idem(request, "fb-cal", operation="feedback"))
    except errors.Forbidden:
        pass
    else:
        raise AssertionError("a customer stamped calibration membership")


async def feedback_ack__an_operator_may_label_a_calibration_set(factory):
    """FEEDBACK-ACK: only an authorized platform operator calibrates."""
    harness = factory()
    request, _ = await _owned_request(harness)
    record = await harness.port.accept(b.auth(role=Role.operator), request.request_id,
                                       {"rating": 1, "calibration_set": "golden"},
                                       b.idem(request, "fb-op", operation="feedback"))
    assert record.calibration_set == "golden" and record.author_role is AuthorRole.operator


def feedback_cases():
    return [feedback_ack__acceptance_is_durable_and_provenance_is_server_set,
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
    submitting = await harness.port.begin_submit(first.run_id)
    await harness.port.record_submission(submitting.run_id, "batch_placeholder_1")
    ambiguous = await harness.port.quarantine(first.run_id, "submission timeout")
    assert ambiguous.state in {JudgeRunState.ambiguous, JudgeRunState.quarantined}
    try:
        await harness.port.reserve(_run(harness), b.consent(), Decimal("0.50"))
    except errors.BudgetExceeded:
        pass
    else:
        raise AssertionError("an ambiguous run freed its reservation")


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
    """JUDGE-BUDGET: current consent is required at submission time."""
    harness = factory(judge_mode="live", budget="1.00")
    revoked = b.consent(revoked_at="2026-09-10T00:00:00Z")
    for consent in (revoked, b.consent(evaluation=False), b.consent(mode=TraceMode.minimal)):
        try:
            await harness.port.reserve(_run(harness), consent, Decimal("0.10"))
        except errors.ConsentMissing:
            pass
        else:
            raise AssertionError("a run without current consent was reserved")


def judge_cases():
    return [judge_budget__the_default_is_dry_run_with_no_authorization,
            judge_budget__reservations_include_outstanding_and_ambiguous_runs,
            judge_budget__one_submission_intent_per_run,
            judge_budget__an_ambiguous_run_never_resubmits,
            judge_budget__settlement_cannot_exceed_the_reservation,
            judge_budget__revoked_or_missing_consent_is_refused_before_egress]
