#!/usr/bin/env python3
"""F-CONTRACT: the serialized contract. Every fixture parses into its model and
serializes back to the identical bytes, every enum matches 08 §3 exactly, and the
error table maps codes to the documented status/type with safe messages only.

    uv run --frozen pytest -q tests/contracts/test_fixtures.py
"""
from __future__ import annotations

import json

import pytest
from infrx.contracts import errors, fixtures, ids, records, wire
from infrx.contracts.codec import canonical_bytes

ALL_FIXTURES = fixtures.names()


def test_every_fixture_is_claimed_by_exactly_one_group():
    claimed = set(fixtures.MODELS) | set(fixtures.LIST_MODELS) | set(fixtures.TABLES)
    assert set(ALL_FIXTURES) == claimed, set(ALL_FIXTURES) ^ claimed
    assert not set(fixtures.MODELS) & set(fixtures.LIST_MODELS)


@pytest.mark.parametrize("name", sorted(fixtures.MODELS))
def test_fixture_round_trips_byte_stably(name):
    """F-CONTRACT: file -> model -> file is the identity."""
    parsed = fixtures.MODELS[name].model_validate(fixtures.load(name))
    assert canonical_bytes(parsed) == fixtures.load_bytes(name)


@pytest.mark.parametrize("name", sorted(fixtures.LIST_MODELS))
def test_list_fixture_round_trips_byte_stably(name):
    model = fixtures.LIST_MODELS[name]
    items = [model.model_validate(item) for item in fixtures.load(name)]
    assert canonical_bytes([item.model_dump(mode="json", exclude_none=True) for item in items]) \
        == fixtures.load_bytes(name)


@pytest.mark.parametrize("name", sorted(fixtures.MODELS))
def test_fixture_rejects_an_unknown_field(name):
    """extra="forbid": a producer cannot add a field without a contract revision."""
    payload = fixtures.load(name)
    payload["definitely_not_a_contract_field"] = 1
    with pytest.raises(Exception):
        fixtures.MODELS[name].model_validate(payload)


def test_no_fixture_leaks_a_secret_or_a_signed_url():
    """Fixtures ship in the repository: no credentials, no signed URLs."""
    forbidden = ("x-amz-signature", "X-Amz-Credential", "?Signature=", "sk-", "eyJhbGciOi",
                 "service_role", "SUPABASE", "https://s3.", "AKIA")
    for name in ALL_FIXTURES:
        text = fixtures.load_bytes(name).decode()
        for needle in forbidden:
            assert needle not in text, f"{name} contains {needle!r}"


# --- vocabulary (08 §3) ------------------------------------------------------
EXPECTED_ENUMS = {
    records.JobState: ["preparing", "queued", "running", "succeeded", "failed", "cancelled",
                       "expired"],
    records.ExecutionMode: ["sync", "stream", "async"],
    records.TerminalCause: ["completed", "client_cancelled", "client_disconnected", "sync_deadline",
                            "queue_wait_expired", "deadline_exceeded", "invalid_media",
                            "preparation_failed", "engine_error", "engine_incomplete",
                            "lost_after_publication", "journal_write_failed", "retries_exhausted",
                            "platform_error"],
    records.UsageCertainty: ["authoritative", "unknown"],
    records.SettlementState: ["settled", "released_free", "held_unknown",
                              "released_platform_absorbed"],
    records.HoldState: ["held", "settled", "released", "unknown"],
    records.ReservationKind: ["preparation", "inference", "journal_bytes"],
    records.ChunkEventType: ["progress", "delta", "usage", "error", "terminal"],
    records.OutboxKind: ["prepare_dispatch", "inference_dispatch", "usage_projection",
                         "trace_projection", "feedback_projection", "judge_projection",
                         "callback_delivery"],
    records.TraceMode: ["off", "minimal", "full"],
    records.TraceLossReason: ["none", "memory_budget", "metadata_budget", "queue_full",
                              "disk_budget", "disk_error", "shutdown", "malformed"],
    records.TraceOfferResult: ["accepted_in_memory", "dropped"],
    records.FeedbackChannel: ["api", "console"],
    records.AuthorRole: ["customer", "operator", "judge"],
    records.JudgeRunState: ["dry_run", "reserved", "submitting", "submitted", "ambiguous",
                            "collecting", "settled", "quarantined", "cancelled"],
    records.UploadState: ["created", "finalized", "aborted", "expired"],
    records.Role: ["owner", "member", "operator", "service"],
    records.ContentState: ["available", "metadata_only", "pending", "lost", "expired", "off"],
}


@pytest.mark.parametrize("enum_type", list(EXPECTED_ENUMS), ids=lambda e: e.__name__)
def test_enum_values_are_frozen(enum_type):
    assert [member.value for member in enum_type] == EXPECTED_ENUMS[enum_type]


def test_public_identifier_shapes():
    request_id = ids.new_request_id()
    assert ids.is_request_id(request_id)
    assert ids.chat_completion_id(request_id) == f"chatcmpl-{request_id}"
    handle = ids.new_job_handle()
    assert handle.startswith("job_") and handle[4:] not in request_id
    assert ids.require_handle(handle, ids.JOB_HANDLE_RE) == handle
    assert ids.new_job_handle() != ids.new_job_handle()      # random, not derived
    for bad in ["not-a-uuid", request_id.upper(), "", None]:
        assert not ids.is_request_id(bad)


def test_cursor_token_round_trips_and_rejects_garbage():
    cursor = records.Cursor(generation=3, sequence=17)
    assert cursor.token == "3-17"
    assert records.Cursor.parse("3-17") == cursor
    for bad in ["3", "3-", "-3", "a-b", "3-17-2", "", "3 - 17", None]:
        with pytest.raises(errors.InvalidCursor):
            records.Cursor.parse(bad)


# --- error envelopes ---------------------------------------------------------
ENVELOPES = fixtures.load("error_envelopes.json")


def test_error_fixture_covers_every_public_code():
    assert set(ENVELOPES) == set(errors.HTTP_ERRORS) | set(errors.STREAM_CODES)


@pytest.mark.parametrize("code", sorted(ENVELOPES))
def test_error_envelope_matches_the_table(code):
    entry = ENVELOPES[code]
    body = entry["envelope"]["error"]
    assert body["code"] == code
    assert body["message"] == errors.MESSAGES[code]
    assert body["type"] == errors.error_type(code)
    if code in errors.HTTP_ERRORS:
        assert entry["http_status"] == errors.http_status(code)
        assert entry["in_stream_only"] is False
    else:
        assert entry["http_status"] is None and entry["in_stream_only"] is True
    if code in errors.RETRY_AFTER_CODES:
        assert body["infrx"]["retry_after_s"] >= 1
    # the envelope itself is the frozen model and round-trips
    envelope = errors.ErrorEnvelope.model_validate(entry["envelope"])
    assert json.loads(canonical_bytes(envelope)) == entry["envelope"]


def test_error_messages_are_fixed_and_safe():
    assert set(errors.MESSAGES) == errors.ALL_CODES
    for code, message in errors.MESSAGES.items():
        assert message and message[0].isupper() and message.endswith(("." , "?")), code
        lowered = message.lower()
        for leak in ("traceback", "psycopg", "select ", "s3://", "http://", "https://", "token"):
            assert leak not in lowered, (code, leak)


def test_internal_codes_have_no_http_mapping():
    """A route that lets stale_lease escape has a bug; it must not become a 500."""
    for code in errors.INTERNAL_CODES:
        with pytest.raises(LookupError):
            errors.http_status(code)
        with pytest.raises(LookupError):
            errors.envelope(errors.DomainError(code=code))


def test_retry_after_is_mandatory_where_the_table_says_so():
    with pytest.raises(ValueError):
        errors.envelope(errors.DomainError(code="rate_limited"))
    assert errors.envelope(errors.RateLimited(retry_after_s=2)).error.infrx == {"retry_after_s": 2}


def test_domain_error_detail_never_reaches_the_envelope():
    err = errors.NotFound("job 4d4d… belongs to org 1a1a… not 2b2b…")
    envelope = errors.envelope(err, "4d4d4d4d-0000-4000-8000-000000000004")
    assert envelope.error.message == errors.MESSAGES["not_found"]
    assert "1a1a" not in canonical_bytes(envelope).decode()


# --- streams -----------------------------------------------------------------
def test_sse_transcript_renders_keepalive_delta_usage_and_sentinel():
    """API-STREAM: the wire form of an accepted stream, keepalives included."""
    transcript = wire.SseTranscript.model_validate(fixtures.load("chat_stream_sse.json"))
    rendered = transcript.render()
    assert rendered.startswith(": keepalive\n\n")
    assert "\nid: 1-3\ndata: {" in rendered
    assert rendered.endswith("id: 1-6\ndata: [DONE]\n\n")
    types = [frame.event_type for frame in transcript.frames if frame.event_type]
    assert types == [records.ChunkEventType.progress, records.ChunkEventType.delta,
                     records.ChunkEventType.delta, records.ChunkEventType.delta,
                     records.ChunkEventType.usage, records.ChunkEventType.terminal]
    usage = [f for f in transcript.frames if f.event_type == records.ChunkEventType.usage][0]
    assert usage.data["usage"]["total_tokens"] == 1540
    ids_seen = [f.id for f in transcript.frames if f.id]
    assert ids_seen == [f"1-{n}" for n in range(1, 7)]       # generation-sequence, monotonic


def test_interrupted_stream_is_honest_about_its_outcome():
    """A terminal error event may precede [DONE]; the outcome stays a failure."""
    transcript = wire.SseTranscript.model_validate(
        fixtures.load("chat_stream_interrupted_sse.json"))
    error_frames = [f for f in transcript.frames
                    if f.event_type == records.ChunkEventType.error]
    assert len(error_frames) == 1
    envelope = errors.ErrorEnvelope.model_validate(error_frames[0].data)
    assert envelope.error.code == "stream_interrupted"
    assert transcript.frames[-1].data == wire.DONE


def test_terminal_outcome_invariants():
    """Unknown usage is usage=None with held_unknown and a reconcile deadline."""
    unknown = fixtures.model("terminal_unknown_usage.json")
    assert unknown.usage is None
    assert unknown.settlement_state is records.SettlementState.held_unknown
    assert unknown.reconcile_after is not None and unknown.debit == 0

    success = fixtures.model("terminal_success.json")
    price = fixtures.model("price_snapshot.json")
    assert success.usage.certainty is records.UsageCertainty.authoritative
    assert success.debit == price.debit(success.usage.prompt_tokens,
                                       success.usage.completion_tokens)
    assert fixtures.model("terminal_platform_error.json").debit == 0

    with pytest.raises(ValueError):       # a debit without a settlement
        records.TerminalOutcome(job_id=unknown.job_id, state=records.JobState.succeeded,
                                cause=records.TerminalCause.completed,
                                settlement_state=records.SettlementState.released_free,
                                debit="0.00000100", settled_at=unknown.settled_at)
    with pytest.raises(ValueError):       # usage that is not authoritative
        records.TerminalOutcome(job_id=unknown.job_id, state=records.JobState.succeeded,
                                cause=records.TerminalCause.completed,
                                usage=records.Usage.of(1, 1, records.UsageCertainty.unknown),
                                settlement_state=records.SettlementState.settled,
                                debit="0.00000100", settled_at=unknown.settled_at)
    with pytest.raises(ValueError):       # a nonterminal state in a terminal outcome
        records.TerminalOutcome(job_id=unknown.job_id, state=records.JobState.running,
                                cause=records.TerminalCause.completed,
                                settlement_state=records.SettlementState.released_free,
                                settled_at=unknown.settled_at)


def test_usage_totals_must_add_up():
    with pytest.raises(ValueError):
        records.Usage(prompt_tokens=10, completion_tokens=5, total_tokens=16)


def test_trace_envelope_cannot_claim_complete_content_after_loss():
    lossy = fixtures.model("trace_envelope_lossy.json")
    assert lossy.content_complete is False
    assert lossy.loss_reason is records.TraceLossReason.memory_budget and lossy.content_ref is None
    with pytest.raises(ValueError):
        records.TraceEnvelope(**{**fixtures.load("trace_envelope.json"),
                                 "loss_reason": "memory_budget"})


def test_client_feedback_submission_cannot_set_provenance():
    """FEEDBACK-ACK: channel, author role and calibration are server-set."""
    with pytest.raises(Exception):
        wire.FeedbackSubmission.model_validate(
            {**fixtures.load("feedback_accepted.json"), "author_role": "operator"})
    accepted = wire.FeedbackAccepted.model_validate(fixtures.load("feedback_accepted.json"))
    assert accepted.channel is records.FeedbackChannel.api
