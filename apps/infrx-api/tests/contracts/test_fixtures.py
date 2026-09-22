#!/usr/bin/env python3
"""F-CONTRACT: the serialized contract. Every fixture parses into its model and
serializes back to the identical bytes, every enum matches 08 §3 exactly, and the
error table maps codes to the documented status/type with safe messages only.

    uv run --frozen pytest -q tests/contracts/test_fixtures.py
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from infrx.contracts import errors, fixtures, ids, limits, records, wire
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


# r1 R47: field names that mean "an object key", plus the credentialed forms of one.
# `wire.py` claims none of its bodies carries a storage key; this is what makes the
# claim checkable instead of a docstring, and it walks every wire fixture (and every
# model in `wire`) rather than the one the ruling happened to name.
STORAGE_KEY_FIELDS = frozenset({
    "storage_ref", "content_ref", "storage_key", "object_key", "s3_key", "s3_uri",
    "bucket", "key_prefix", "signed_url", "presigned_url", "download_url", "object_path",
})
# `destination_ref` on `UploadCreated` is deliberately not a key: it is a constrained
# server-issued reference (`infrx-upload:upl_<id>`, R61 (1)), and the test below asserts
# that shape rather than trusting the name.
WIRE_FIXTURES = tuple(sorted(name for name, model in fixtures.MODELS.items()
                             if model.__module__.endswith("contracts.wire")))


def _field_names(data, into=None):
    into = set() if into is None else into
    if isinstance(data, dict):
        for key, value in data.items():
            into.add(key)
            _field_names(value, into)
    elif isinstance(data, (list, tuple)):
        for item in data:
            _field_names(item, into)
    return into


def test_the_wire_fixtures_are_the_public_bodies():
    """The grep below is only worth its coverage: every `wire` model with a fixture is
    in it, and the trace export - the body R47 is about - is one of them."""
    assert "trace_export.json" in WIRE_FIXTURES
    assert len(WIRE_FIXTURES) >= 10, WIRE_FIXTURES


@pytest.mark.parametrize("name", WIRE_FIXTURES)
def test_no_wire_fixture_carries_a_storage_key(name):
    """r1 R47: no public body carries an object key or a signed URL, at any depth.

    The trace export is the case the ruling names - `content_ref` is an S3 key, so the
    export carries `content_state` plus an opaque `content_handle` instead - but the rule
    is the whole module's, so every wire fixture is checked.
    """
    found = _field_names(fixtures.load(name)) & STORAGE_KEY_FIELDS
    assert found == set(), f"{name} carries storage-key-shaped field(s) {sorted(found)}"


@pytest.mark.parametrize("name", WIRE_FIXTURES)
def test_no_wire_model_declares_a_storage_key(name):
    """The same rule on the model, so a field nobody put in a fixture cannot slip in."""
    model = fixtures.MODELS[name]
    found = set(model.model_fields) & STORAGE_KEY_FIELDS
    assert found == set(), f"{model.__name__} declares {sorted(found)}"


def test_the_trace_export_replaces_the_content_ref_with_availability_and_a_handle():
    """r1 R47: the export is a projection of the envelope, not the envelope."""
    export = fixtures.model("trace_export.json")
    envelope = fixtures.model("trace_envelope.json")
    assert envelope.content_ref, "the internal envelope does carry the object key"
    assert export.content_state is records.ContentState.available
    # r1 R54: opaque and prefixed, so a storage key cannot pass for a handle.
    assert export.content_handle and envelope.content_ref not in export.content_handle
    assert export.content_handle.startswith(ids.TRACE_CONTENT_PREFIX)
    for forged in (envelope.content_ref, "tc_short", "", "fb_" + "a" * 30,
                   "tc_/etc/passwd", "s3://bucket/key"):
        with pytest.raises(ValueError):
            wire.TraceExport.of(envelope, records.ContentState.available,
                                export.content, content_handle=forged)
    # and resolved content is reached *through* a handle, never handed over without one
    with pytest.raises(ValueError):
        wire.TraceExport.of(envelope, records.ContentState.available, export.content)
    assert export.content_bytes == envelope.content_bytes
    # the content object is `{v: 1, request, response}` (research/traces/04 §3.1)
    assert export.content is not None and export.content.v == 1
    assert export.content.response.status == 200
    assert set(fixtures.load("trace_export.json")["content"]) == {"v", "request", "response"}
    # and `of()` never copies the key across, whatever it is handed
    projected = wire.TraceExport.of(envelope, records.ContentState.metadata_only)
    assert projected.content_handle is None and projected.content is None
    assert envelope.content_ref not in canonical_bytes(projected).decode()


def test_an_upload_destination_is_a_constrained_reference_not_a_url():
    """The one `*_ref` a public body does carry: a server-issued upload destination.
    It is asserted by shape, so a signed URL cannot arrive under an innocent name."""
    created = fixtures.model("upload_created.json")
    assert created.destination_ref.startswith("infrx-upload:")
    assert "://" not in created.destination_ref and "?" not in created.destination_ref
    # R61 (1): no organization qualifier. The fixture carried `infrx-upload:pilot:upl_…`
    # until the F2R lane-A revision (coordinator ruling: R61 supersedes the wave-2 byte).
    assert created.destination_ref == f"infrx-upload:{created.upload_handle}"
    assert ids.UPLOAD_HANDLE_RE.fullmatch(created.upload_handle)
    # F2R coordinator addition 5: `video/mpeg` is not a pilot input, so the example no
    # longer offers it. These are the two sanctioned v1 fixture byte changes.
    assert created.accepted_mime == ("video/mp4", "video/webm", "video/quicktime")


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
                              "disk_budget", "disk_error", "shutdown", "malformed",
                              # r1 R37
                              "abandoned"],
    records.TraceOfferResult: ["accepted_in_memory", "dropped"],
    records.FeedbackChannel: ["api", "console"],
    records.AuthorRole: ["customer", "operator", "judge"],
    # r1 R3 / R8 additions to 08 §3
    # r1 R43: the entry names. The submittable subset is FEEDBACK_INPUT_NAMES.
    records.FeedbackName: ["thumb", "rating", "correction", "comment",
                           "calibration_label"],
    records.CalibrationLabel: ["correct", "partially_correct", "incorrect",
                               "unusable"],
    records.JudgeResolution: ["adopt_provider_evidence", "release_reservation"],
    records.JudgeRunState: ["dry_run", "reserved", "submitting", "submitted", "ambiguous",
                            "collecting", "settled", "quarantined", "cancelled"],
    records.UploadState: ["created", "finalized", "aborted", "expired"],
    records.Role: ["owner", "member", "operator", "service"],
    records.ContentState: ["available", "metadata_only", "pending", "lost", "expired", "off"],
    # F2R IR-7: the console's ACCOUNTING_REGIMES
    records.AccountingRegime: ["legacy_usd", "pilot"],
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
    for bad in ["not-a-uuid", request_id.upper(), "", None,
                request_id + "\n", request_id + " ", request_id + "x"]:
        assert not ids.is_request_id(bad), bad
    for bad in (handle + "\n", handle + "/../other"):
        with pytest.raises(ValueError):
            ids.require_handle(bad, ids.JOB_HANDLE_RE)


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


def test_error_code_table_fixture_is_the_python_table():
    """r1 R13: `ERROR_CODE_HTTP_STATUS` in the console must equal this table, and the
    G0 parity test compares both halves against this one file."""
    table = fixtures.load("error_codes.json")
    assert set(table) == {"http", "in_stream_only", "internal_only", "retry_after_required"}
    assert set(table["http"]) == set(errors.HTTP_ERRORS)
    for code, entry in table["http"].items():
        assert entry == {"status": errors.http_status(code), "type": errors.error_type(code)}
    assert set(table["in_stream_only"]) == set(errors.STREAM_CODES)
    for code, entry in table["in_stream_only"].items():
        assert entry == {"type": errors.error_type(code)}
    assert set(table["internal_only"]) == set(errors.INTERNAL_CODES)
    assert set(table["retry_after_required"]) == set(errors.RETRY_AFTER_CODES)
    assert not set(table["http"]) & set(table["internal_only"])


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


def test_cause_and_state_must_agree():
    """The pair is one fact: a succeeded job whose cause is `engine_error` would be
    a free success, and a failed job whose cause is `completed` would lose a debit."""
    settled = fixtures.model("terminal_success.json")
    for state, cause in ((records.JobState.succeeded, records.TerminalCause.engine_error),
                         (records.JobState.failed, records.TerminalCause.completed),
                         (records.JobState.succeeded, records.TerminalCause.client_cancelled),
                         (records.JobState.cancelled, records.TerminalCause.queue_wait_expired)):
        with pytest.raises(ValueError):
            records.TerminalOutcome(job_id=settled.job_id, state=state, cause=cause,
                                    settlement_state=records.SettlementState.released_free,
                                    settled_at=settled.settled_at)
    assert records.states_for_cause(records.TerminalCause.platform_error) == \
        frozenset({records.JobState.failed})


def test_price_rates_are_never_negative():
    """A negative rate would turn a settlement debit into a credit."""
    raw = fixtures.load("price_snapshot.json")
    with pytest.raises(ValueError):
        records.PriceSnapshot(**{**raw, "input_rate_per_million": "-0.20000000"})
    with pytest.raises(ValueError):
        records.PriceSnapshot(**{**raw, "output_rate_per_million": "-0.60000000"})


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


def test_an_abandoned_capture_is_an_honest_envelope():
    """r1 R37: a reaped or abandoned capture keeps no content and says why."""
    raw = fixtures.load("trace_envelope_abandoned.json")
    envelope = records.TraceEnvelope.model_validate(raw)
    assert envelope.loss_reason is records.TraceLossReason.abandoned
    assert envelope.content_bytes == 0 and envelope.content_ref is None
    assert envelope.content_complete is False and envelope.carries_content is False
    with pytest.raises(ValueError):          # an abandoned capture is never complete
        records.TraceEnvelope(**{**raw, "content_complete": True,
                                 "content_ref": "traces/x.json.zst"})


def test_only_full_mode_carries_trace_content():
    """r1 R12: `minimal` is metadata only and `off` has no row at all, so content
    tied to anything but `full` is a malformed envelope."""
    full = fixtures.load("trace_envelope.json")
    for mode in ("minimal", "off"):
        with pytest.raises(ValueError):
            records.TraceEnvelope(**{**full, "mode": mode})
        metadata_only = records.TraceEnvelope(**{**full, "mode": mode, "content_bytes": 0,
                                                 "content_complete": False, "content_ref": None})
        assert metadata_only.carries_content is False
    assert records.TraceEnvelope(**full).carries_content is True


def test_admission_carries_the_budgets_it_was_accepted_with():
    """r1 R4: a configuration change after acceptance cannot move an accepted job's
    deadlines, so the budgets are on the record."""
    admission = fixtures.model("admission.json")
    assert admission.budgets.queue_wait_s == limits.DEFAULTS.queue_wait_interactive_s
    assert admission.budgets.generation_s == limits.DEFAULTS.generation_timeout_s
    assert admission.budgets.first_token_s == limits.DEFAULTS.ttft_timeout_s
    asynchronous = records.Budgets.of(limits.DEFAULTS, records.ExecutionMode.async_)
    assert asynchronous.queue_wait_s == limits.DEFAULTS.queue_wait_async_s
    with pytest.raises(ValueError):
        records.Budgets(**{**admission.budgets.model_dump(), "queue_wait_s": -1})
    raw = fixtures.load("admission.json")
    raw.pop("budgets")
    with pytest.raises(ValueError):          # not optional: an accepted job has them
        records.Admission.model_validate(raw)


def test_phase_deadline_instants_live_on_the_records():
    """r1 R20: the store derives each phase instant at the transition into that phase
    and hands it to whoever enforces it — the reaper on the admission, the worker on
    its lease — and no phase instant outlives `deadline_at`."""
    admission = fixtures.model("admission.json")
    assert admission.preparation_deadline_at == admission.admitted_at + timedelta(
        seconds=admission.budgets.preparation_s)
    assert admission.preparation_deadline_at <= admission.deadline_at
    assert admission.queue_deadline_at is None, "a preparing job has no queue instant yet"
    queued = fixtures.model("admission_replay.json")
    assert queued.queue_deadline_at is not None and queued.queue_deadline_at <= queued.deadline_at
    raw = fixtures.load("admission.json")
    raw.pop("preparation_deadline_at")
    with pytest.raises(ValueError):          # every accepted job has one
        records.Admission.model_validate(raw)

    lease = fixtures.model("lease.json")
    assert lease.first_token_deadline_at <= lease.generation_deadline_at
    raw = fixtures.load("lease.json")
    with pytest.raises(ValueError):          # a first token cannot outlast generation
        records.Lease.model_validate({**raw,
                                      "first_token_deadline_at": raw["generation_deadline_at"],
                                      "generation_deadline_at": raw["first_token_deadline_at"]})
    for field in ("generation_deadline_at", "first_token_deadline_at"):
        with pytest.raises(ValueError):
            records.Lease.model_validate({k: v for k, v in raw.items() if k != field})


FEEDBACK_VALUES = [("thumb", True, True), ("thumb", False, True), ("thumb", 1, False),
                   ("rating", 1, True), ("rating", 5, True), ("rating", 0, False),
                   ("rating", 6, False), ("rating", True, False), ("rating", 1.0, False),
                   ("correction", "two people", True), ("correction", "", False),
                   ("correction", "  ", False), ("comment", "fine", True),
                   ("comment", 3, False)]


@pytest.mark.parametrize("name,value,valid", FEEDBACK_VALUES,
                         ids=[f"{n}-{v!r}" for n, v, _ in FEEDBACK_VALUES])
def test_the_feedback_name_fixes_the_value_type(name, value, valid):
    """r1 R3 / `research/traces/06` §2: thumb is boolean, rating is an integer 1-5,
    correction and comment are nonempty text. A JSON float is never a rating."""
    raw = {**fixtures.load("feedback.json"), "name": name, "value": value}
    body = {"request_id": raw["request_id"], "name": name, "value": value}
    if valid:
        assert records.Feedback.model_validate(raw).value == value
        assert wire.FeedbackSubmission.model_validate(body).value == value
    else:
        with pytest.raises(ValueError):
            records.Feedback.model_validate(raw)
        with pytest.raises(ValueError):
            wire.FeedbackSubmission.model_validate(body)


# r1 R43/R50/R54: the calibration fields are one fact, and the **record** is what
# enforces it. The fake never builds an inconsistent row, so without these the validator
# was unkillable: a mutant deleting a clause survived every conformance case.
def _label_row():
    return fixtures.load("feedback_calibration_label.json")


def _plain_row():
    return fixtures.load("feedback.json")


INCONSISTENT_FEEDBACK = [
    # a label missing any one of the three facts that make it a label
    ("label without membership", {**_label_row(), "calibration_set": False}),
    ("label without a rubric version", {k: v for k, v in _label_row().items()
                                        if k != "rubric_version"}),
    ("label authored by a customer", {**_label_row(), "author_role": "customer"}),
    ("label not made by an operator", {**_label_row(), "by_operator": False}),
    # r1 R55: the converse, on any row. `author_role=operator` without the marker was
    # constructible, and the marker is what the masking keys on - so the row would be
    # operator-authored and read to a customer with the operator's principal intact.
    ("operator author without the marker",
     {**_plain_row(), "author_role": "operator", "by_operator": False}),
    # an ordinary entry claiming any one of them
    ("plain row claiming membership", {**_plain_row(), "calibration_set": True}),
    ("plain row carrying a rubric version", {**_plain_row(), "rubric_version": 3}),
    ("plain row named a label", {**_plain_row(), "name": "calibration_label"}),
    # and a label whose value is not a label
    ("label with a free-text verdict", {**_label_row(), "value": "golden"}),
    ("label with a numeric verdict", {**_label_row(), "value": 3}),
]


@pytest.mark.parametrize("what,raw", INCONSISTENT_FEEDBACK, ids=[c[0] for c in INCONSISTENT_FEEDBACK])
def test_the_record_refuses_every_inconsistent_calibration_row(what, raw):
    with pytest.raises(ValueError):
        records.Feedback.model_validate(raw)


def test_a_consistent_calibration_row_is_accepted():
    """The other half: the nine refusals above mean nothing if the valid row is refused
    too."""
    assert records.Feedback.model_validate(_label_row()).calibration_set is True
    assert records.Feedback.model_validate(_plain_row()).rubric_version is None


# r1 R54: strict where the ports are. Pydantic's lax mode read `True` as 1 and `"3"` as
# three, so the record was looser than `label_calibration` - and a caller reaching the
# record directly (D's adapter, a projection) got the loose behaviour.
STRICT_INTEGER_FIELDS = [
    ("rubric_version", True), ("rubric_version", "3"), ("rubric_version", 3.0),
    ("rubric_version", 3.5),
]


@pytest.mark.parametrize("field,value", STRICT_INTEGER_FIELDS,
                         ids=[f"{f}={v!r}" for f, v in STRICT_INTEGER_FIELDS])
def test_a_rubric_version_is_a_strict_integer(field, value):
    with pytest.raises(ValueError):
        records.Feedback.model_validate({**_label_row(), field: value})
    with pytest.raises(ValueError):
        raw = fixtures.load("judge_runs.json")[0]
        records.JudgeRun.model_validate({**raw, field: value})


@pytest.mark.parametrize("value", [True, False, "8", 8.0, 8.5])
def test_an_entitlement_limit_is_a_strict_integer(value):
    """r1 R52/R54: booleans are rejected in both halves. `True` as a limit of 1 is a
    concurrency cap of one request, silently."""
    raw = fixtures.load("org_entitlements.json")
    with pytest.raises(ValueError):
        records.OrgEntitlements.model_validate(
            {**raw, "limits": {**raw["limits"], "max_concurrent_requests": value}})


# The store mints these freshly on every admission, so a fixture cannot pin them; every
# other field of `admission.json` is the store's own derivation and is compared exactly.
FRESHLY_MINTED = ("job_handle", "outbox")


def test_the_request_fixture_is_one_the_store_would_admit():
    """F-CONTRACT: `admission.json` is what `admit` returns for `normalized_request.json`.

    It used to be asserted by eye, and two things had drifted: the request's `deadline_at`
    was `created_at + 450 s` where a **stream** request's bound is 120 + 10 + 300 = 430 s,
    so the store would have refused the very request the admission fixture claims it
    admitted; and `maximum_hold` was `0.00768000` where the ceiling formula on the
    fixture's own rates and ceilings gives `0.00629760`. Both are the kind of drift only a
    real call can find, so this one makes the call - on the fake, with the clock at the
    fixture's `created_at` and its own price snapshot seeded - and compares the returned
    record field by field.
    """
    request = fixtures.model("normalized_request.json")
    expected = fixtures.model("admission.json")
    # r1 R55: inside the ceiling ranges, which is the cheap half of the check
    assert 1 <= request.max_output_tokens <= limits.DEFAULTS.max_output_tokens
    assert request.max_input_tokens >= 1
    assert (request.max_input_tokens + request.max_output_tokens
            <= limits.DEFAULTS.max_context_tokens)
    prepared = fixtures.model("prepared_request.json")
    assert 1 <= prepared.max_output_tokens <= limits.DEFAULTS.max_output_tokens

    admission = _admit_the_fixture(request, expected)
    for field in records.Admission.model_fields:
        if field in FRESHLY_MINTED:
            continue
        assert getattr(admission, field) == getattr(expected, field), \
            f"admission.json disagrees with the store on {field}: " \
            f"{getattr(expected, field)!r} vs {getattr(admission, field)!r}"
    # the two a fixture cannot pin are still checked for shape
    assert ids.JOB_HANDLE_RE.fullmatch(admission.job_handle)
    assert [event.kind for event in admission.outbox] == \
        [event.kind for event in expected.outbox]
    assert [event.payload["request_id"] for event in admission.outbox] == \
        [request.request_id for _ in expected.outbox]


def _admit_the_fixture(request, expected):
    """Admit `normalized_request.json` on the fake, at its own `created_at`."""
    import asyncio
    from infrx.contracts.fakes.state import FakeJobStore
    from infrx.contracts.fakes.support import FakeClock, SequentialIds

    async def run():
        store = FakeJobStore(FakeClock(request.created_at), SequentialIds(),
                             prices={request.model_revision: expected.price_snapshot})
        store.grant(request.org_id, "25.00")
        idem = records.IdempotencyRef(org_id=request.org_id, operation=expected.operation,
                                      key=expected.idempotency_key,
                                      payload_hash=expected.payload_hash)
        return await store.admit(request, idem, ())

    return asyncio.run(run())


def test_client_feedback_submission_cannot_set_provenance():
    """FEEDBACK-ACK: channel, author role and calibration are server-set, and an
    empty body is not a submission (r1 R3)."""
    with pytest.raises(Exception):
        wire.FeedbackSubmission.model_validate(
            {**fixtures.load("feedback_accepted.json"), "author_role": "operator"})
    accepted = wire.FeedbackAccepted.model_validate(fixtures.load("feedback_accepted.json"))
    assert accepted.channel is records.FeedbackChannel.api
    feedback = fixtures.model("feedback.json")
    for body in ({}, {"request_id": feedback.request_id},
                 {"request_id": feedback.request_id, "name": "rating"},
                 {"request_id": feedback.request_id, "value": 3},
                 {"request_id": feedback.request_id, "name": "rating", "value": 3,
                  "calibration_set": "golden"}):
        with pytest.raises(Exception):
            wire.FeedbackSubmission.model_validate(body)


# --- F2R item 5: judge sample ids and the frozen judge-sample DTO --------------------
def test_judge_run_sample_ids_are_unique_lowercase_uuid4():
    raw = fixtures.load("judge_runs.json")[0]
    one = "00000000-0000-4000-8000-0000000000aa"
    assert records.JudgeRun.model_validate({**raw, "sample_ids": [one]}).sample_ids == (one,)
    for bad in ([one, one], [one.upper()], ["s1"], [one[:-1] + "g"]):
        with pytest.raises(ValueError):
            records.JudgeRun.model_validate({**raw, "sample_ids": bad})


def test_the_judge_sample_dto_is_the_consoles_four_fields():
    """IR-7: `{sample_id, rubric_version, request_id, scores}`, `request_id` nullable."""
    assert [name for name in records.JudgeSample.model_fields if name != "schema_version"] \
        == ["sample_id", "rubric_version", "request_id", "scores"]
    one = "00000000-0000-4000-8000-0000000000aa"
    sample = records.JudgeSample(sample_id=one, rubric_version=1, request_id=None)
    assert sample.request_id is None and sample.scores == ()
    for bad in ({"sample_id": "s1"}, {"rubric_version": True}, {"request_id": "r1"}):
        with pytest.raises(ValueError):
            records.JudgeSample.model_validate({"sample_id": one, "rubric_version": 1,
                                                "request_id": one, **bad})
    with pytest.raises(ValueError):                 # present, even when null
        records.JudgeSample.model_validate({"sample_id": one, "rubric_version": 1})
