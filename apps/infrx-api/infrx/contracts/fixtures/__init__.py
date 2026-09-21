"""Serialized contracts v1: the bytes every track must agree on.

Each file under `v1/` is canonical JSON (`codec.canonical_bytes`): sorted keys,
two-space indent, trailing newline, no nulls. `MODELS` and `LIST_MODELS` say
which model owns which file, so `tests/contracts/test_fixtures.py` can prove
every file round-trips back to identical bytes, and `test_fixtures` fails on any
file nobody claims - an unclassified fixture is a hole in the contract.

`error_envelopes.json`, `error_codes.json`, `money_cases.json` and
`money_tables.json` are tables rather than single records and are checked by
their own tests.

`money_cases.json` and `error_codes.json` are the cross-language parity contract
(08 §10 R11, R13): the TypeScript half must accept and reject exactly the same
money inputs and map the same codes to the same statuses. `money_cases.json` is
therefore **byte-identical** to the console half's copy at
`apps/app/tests/contracts/money_cases.json`: a list of `{input, valid,
canonical}` sorted by `input`, with `canonical: null` where the input is
invalid. It is the one fixture that does not follow the no-nulls rule, because
matching the other language exactly matters more than the house style, and the
coordinator diffs the two files at integration. `money_tables.json` holds the
Python-side debit, hold and ledger-delta tables that were in `money_cases.json`
before r1.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

from pydantic import BaseModel

from .. import records, wire

DIR = pathlib.Path(__file__).parent / "v1"

MODELS: dict[str, type[BaseModel]] = {
    # records
    "admission.json": records.Admission,
    "admission_replay.json": records.Admission,
    "auth_context.json": records.AuthContext,
    "chunk.json": records.Chunk,
    "consent_snapshot.json": records.ConsentSnapshot,
    "cursor.json": records.Cursor,
    "idempotency_ref.json": records.IdempotencyRef,
    "index_event.json": records.IndexEvent,
    "lease.json": records.Lease,
    "media_ref.json": records.MediaRef,
    "normalized_request.json": records.NormalizedRequest,
    "prepared_request.json": records.PreparedRequest,
    "price_snapshot.json": records.PriceSnapshot,
    "terminal_cancelled.json": records.TerminalOutcome,
    "terminal_expired.json": records.TerminalOutcome,
    "terminal_platform_error.json": records.TerminalOutcome,
    "terminal_success.json": records.TerminalOutcome,
    "terminal_unknown_usage.json": records.TerminalOutcome,
    "trace_envelope.json": records.TraceEnvelope,
    "trace_envelope_lossy.json": records.TraceEnvelope,
    "trace_envelope_abandoned.json": records.TraceEnvelope,
    "feedback.json": records.Feedback,
    # r1 R43: the one shape a calibration label takes - the same record, with
    # `name=calibration_label`, `calibration_set=true` and an integer rubric version.
    "feedback_calibration_label.json": records.Feedback,
    "org_entitlements.json": records.OrgEntitlements,       # r1 R24
    # wire
    "chat_stream_interrupted_sse.json": wire.SseTranscript,
    "chat_stream_sse.json": wire.SseTranscript,
    "chat_success_nonstream.json": wire.ChatCompletionResponse,
    "feedback_accepted.json": wire.FeedbackAccepted,
    "job_accepted.json": wire.JobAccepted,
    "job_expired.json": wire.JobStatus,
    "job_idempotent_replay.json": wire.JobAccepted,
    "job_result.json": wire.JobResult,
    "job_status.json": wire.JobStatus,
    "trace_export.json": wire.TraceExport,
    "upload_completed.json": wire.UploadCompleted,
    "upload_created.json": wire.UploadCreated,
}

LIST_MODELS: dict[str, type[BaseModel]] = {
    "engine_events.json": records.EngineEvent,
    "judge_runs.json": records.JudgeRun,
}

# r1 R54: `text_bounds.json` is the third cross-language parity table - boundary
# strings both halves must classify identically, in **code points**. It is
# byte-identical to the console copy at `apps/app/tests/contracts/text_bounds.json`.
TABLES = ("error_envelopes.json", "error_codes.json", "money_cases.json",
          "money_tables.json", "text_bounds.json")


def names() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in DIR.glob("*.json")))


def load_bytes(name: str) -> bytes:
    return (DIR / name).read_bytes()


def load(name: str) -> Any:
    return json.loads(load_bytes(name))


def model(name: str) -> BaseModel:
    """The fixture as its validated model (single-model fixtures only)."""
    return MODELS[name].model_validate(load(name))
