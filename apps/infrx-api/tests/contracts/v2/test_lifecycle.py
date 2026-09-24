#!/usr/bin/env python3
"""F2C.a: the lifecycle cases against the fake, the record rules, and TS parity.

Each case id is `<oracle>__<invariant>` (UPLOAD-RESTART, ADMISSION-READY,
RETENTION-DURABLE of `04-verification.md`); `tests/contracts/mutants.py` names a
single-edit defect for each, and the one runner requires the named case to fail.

    uv run --frozen pytest -q tests/contracts/v2/test_lifecycle.py
"""
from __future__ import annotations

import asyncio
import enum
import inspect
import re
from pathlib import Path

import pydantic
import pytest
from datetime import datetime, timedelta

from infrx.contracts import errors, fixtures as v1fix, records as v1
from infrx.contracts.conformance import lifecycle as suite
from infrx.contracts.fakes.factories import lifecycle_factory
from infrx.contracts.v2 import fixtures as v2fix, lifecycle as lc

from ..test_parity_console import ts_string_array

CASES = suite.cases()
ORACLES = ("upload_restart", "admission_ready", "retention_durable", "result_expiry")
LIFECYCLE_TS = (Path(__file__).resolve().parents[3].parent / "app" / "lib" / "contracts" / "v2"
                / "lifecycle.ts")


@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
def test_the_fake_passes_every_lifecycle_case(case):
    asyncio.run(case(lifecycle_factory))


def test_every_case_names_its_oracle_and_its_invariant():
    for case in CASES:
        assert case.__name__.split("__")[0] in ORACLES, case.__name__
        assert case.__doc__ and inspect.iscoroutinefunction(case), case.__name__
    assert len({case.__name__ for case in CASES}) == len(CASES)
    assert {case.__name__.split("__")[0] for case in CASES} == set(ORACLES)


def test_the_fake_satisfies_the_three_ports_with_async_operations():
    store = lifecycle_factory().port
    for protocol in (lc.UploadRepository, lc.ReadinessStore, lc.ContentLifecycle):
        assert isinstance(store, protocol), protocol.__name__
        for name in protocol.__protocol_attrs__:
            assert inspect.iscoroutinefunction(getattr(store, name)), name


# --- records -----------------------------------------------------------------------------
LIFECYCLE_FIXTURES = sorted(v2fix.LIFECYCLE_MODELS)


@pytest.mark.parametrize("name", LIFECYCLE_FIXTURES)
def test_a_missing_required_or_an_unexpected_field_fails_deterministically(name):
    """Oracle: a decoder that defaults a missing required field (e.g. `sources` -> [])
    or ignores an unknown one (a smuggled `org_id`, an object key) passes this test."""
    model, body = v2fix.MODELS[name], v2fix.load(name)
    assert model.model_validate(body) == v2fix.model(name)
    for key in body:
        dropped = {k: v for k, v in body.items() if k != key}
        if model.model_fields[key].is_required():
            with pytest.raises(pydantic.ValidationError):
                model.model_validate(dropped)
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({**body, "unexpected": 1})


def test_an_empty_manifest_is_a_completed_fact_and_never_the_default():
    """RV-05 at the record: `sources: []` is ready-with-zero; no `sources` is an error;
    the view of a job with no marker carries no count and no instant."""
    body = v2fix.load("lifecycle_readiness_text_only.json")
    assert body["sources"] == [] and lc.ExecutionReadiness.model_validate(body).sources == ()
    with pytest.raises(pydantic.ValidationError):
        lc.ExecutionReadiness.model_validate({k: v for k, v in body.items() if k != "sources"})
    ready = v2fix.model("lifecycle_readiness_view_ready_empty.json")
    missing = v2fix.model("lifecycle_readiness_view_not_ready.json")
    assert (ready.state, ready.source_count) == (lc.ReadinessState.ready, 0)
    assert (missing.state, missing.source_count, missing.ready_at) == (
        lc.ReadinessState.not_ready, None, None)
    view = v2fix.load("lifecycle_readiness_view_ready_empty.json")
    for broken in ({**view, "source_count": None}, {**view, "state": "not_ready"},
                   {k: v for k, v in view.items() if k != "ready_at"}):
        with pytest.raises(pydantic.ValidationError):
            lc.ReadinessView.model_validate({k: v for k, v in broken.items() if v is not None})


def test_a_manifest_holds_only_the_jobs_own_distinct_sources():
    body = v2fix.load("lifecycle_readiness_media.json")
    (source,) = body["sources"]
    foreign = {**source, "ref": {**source["ref"], "org_id": v2fix.IDS.other_org}}
    for sources in ([foreign], [source, source]):
        with pytest.raises(pydantic.ValidationError):
            lc.ExecutionReadiness.model_validate({**body, "sources": sources})


def test_an_upload_ticket_is_one_fact():
    """A finalized source is exactly the received bytes and satisfies every constraint;
    the destination is the handle's; the window is ordered."""
    body = v2fix.load("lifecycle_upload_finalized.json")
    assert lc.UploadTicket.model_validate(body).state.value == "finalized"
    done, got = body["finalized"], body["received"]
    for broken in ({**body, "received": None},
                   {**body, "finalized": {**done, "digest": "sha256:" + "b2" * 32}},
                   {**body, "finalized": {**done, "mime": "video/x-flv"}},
                   {**body, "finalized": {**done, "finalized_at": body["expires_at"]}},
                   {**body, "received": {**got, "bytes": body["constraints"]["max_bytes"] + 1}},
                   {**body, "destination_ref": "s3://bucket/uploads/x"},
                   {**body, "state": "created"},
                   {**body, "state": "aborted"},
                   {**body, "expires_at": body["created_at"]}):
        with pytest.raises(pydantic.ValidationError):
            lc.UploadTicket.model_validate({k: v for k, v in broken.items() if v is not None})
    # With no declared digest or size, only the receipt says which bytes the handle names.
    loose = {**body, "constraints": {k: v for k, v in body["constraints"].items()
                                     if k not in ("digest", "bytes")}}
    assert lc.UploadTicket.model_validate(loose).state.value == "finalized"
    for forged in ({**done, "digest": "sha256:" + "b2" * 32}, {**done, "bytes": 1}):
        with pytest.raises(pydantic.ValidationError):
            lc.UploadTicket.model_validate({**loose, "finalized": forged})
    created = v2fix.load("lifecycle_upload_created.json")
    with pytest.raises(pydantic.ValidationError):
        lc.UploadTicket.model_validate({**created, "state": "aborted"})
    assert lc.UploadTicket.model_validate(
        {**created, "state": "aborted", "refusal": "mime_not_accepted"}).refusal is \
        lc.LifecycleRefusal.mime_not_accepted


def test_constraints_default_to_the_deployment_and_refuse_everything_else():
    parsed = lc.UploadConstraints.parse({}, max_media_bytes=4096,
                                        allowed_mime=frozenset({"video/webm", "video/mp4"}))
    assert (parsed.max_bytes, parsed.accepted_mime) == (4096, ("video/mp4", "video/webm"))
    for body in (None, [], {"bytes": 4097}, {"bytes": 0}, {"digest": "md5:0"},
                 {"accepted_mime": []}, {"accepted_mime": ["video/mp4", "video/mp4"]},
                 {"accepted_mime": ["Video/MP4"]}, {"storage_ref": "media/x"}):
        with pytest.raises(errors.InvalidRequest) as refused:
            lc.UploadConstraints.parse(body, max_media_bytes=4096,
                                       allowed_mime=frozenset({"video/mp4", "Video/MP4"}))
        assert lc.refusal_of(refused.value) is lc.LifecycleRefusal.invalid_constraints, body


def test_a_content_row_state_is_one_fact():
    live = v2fix.load("lifecycle_content_live.json")
    tombstoned = v2fix.load("lifecycle_content_tombstoned.json")
    for broken in ({**live, "tombstoned_at": tombstoned["tombstoned_at"]},
                   {**live, "eligible_at": "2026-09-22T11:00:00Z"},
                   {**tombstoned, "state": "live"},
                   {**tombstoned, "state": "deleted"},
                   {**tombstoned, "claim": {**tombstoned["claim"], "generation": 2}},
                   {**live, "identity": {**live["identity"], "job_id": v2fix.IDS.request}},
                   {**live, "identity": {k: v for k, v in live["identity"].items()
                                         if k != "digest"}}):
        with pytest.raises(pydantic.ValidationError):
            lc.ContentObject.model_validate(broken)


def test_an_admission_expectation_names_exactly_its_regimes_card():
    assert lc.AdmissionExpectation(accounting_regime="legacy_usd").rate_card_version is None
    for body in ({"accounting_regime": "credit"},
                 {"accounting_regime": "legacy_usd", "rate_card_version": "rc"},
                 {"accounting_regime": "credit", "rate_card_version": " rc"},
                 {"accounting_regime": "credit", "rate_card_version": ""}):
        with pytest.raises(pydantic.ValidationError):
            lc.AdmissionExpectation.model_validate(body)


def test_every_refusal_is_an_existing_code_and_internal_ones_never_render():
    """No new public code: the vocabulary maps onto `errors`, and a refusal that must
    never reach a customer (`not_ready`, claims) is an INTERNAL code with no status."""
    table = v2fix.load("lifecycle_refusals.json")
    assert [row["reason"] for row in table] == [r.value for r in lc.LifecycleRefusal]
    internal = {"not_ready", "not_eligible", "reference_live", "claim_held", "claim_lost"}
    for row in table:
        assert row["code"] in errors.ALL_CODES, row
        error = lc.refuse(lc.LifecycleRefusal(row["reason"]), "detail")
        assert error.code == row["code"] and lc.refusal_of(error).value == row["reason"]
        assert (row["code"] in errors.INTERNAL_CODES) == (row["reason"] in internal), row
        if row["code"] in errors.HTTP_ERRORS:
            body = errors.envelope(error).model_dump(mode="json", exclude_none=True)["error"]
            # The public envelope is exactly the code's: the reason never ships.
            assert set(body) <= {"message", "type", "code"} | ({"infrx"} if error.retry_after_s
                                                              else set()), body
            assert body.get("infrx", {}).keys() <= {"retry_after_s"}, body


# --- F2C.b: terminal/read consistency ------------------------------------------------------
def test_an_old_terminal_record_is_never_given_an_invented_expiry():
    """Oracle: decoding a record written before F2C.b and then re-deriving its expiry from
    configuration (settled_at + TTL - `Jobs.result_expiry` today) passes this test."""
    old = v1fix.model("terminal_success.json")
    assert old.result_expires_at is None and "result_expires_at" not in v1fix.load(
        "terminal_success.json")
    for later in (timedelta(0), timedelta(hours=1), timedelta(days=1), timedelta(days=400)):
        assert lc.read_outcome(old, old.settled_at + later) is lc.ReadOutcome.unavailable
    new = v1fix.model("terminal_success_expiring.json")
    assert new.result_expires_at - new.settled_at == timedelta(days=1)
    assert new.model_dump(exclude={"result_expires_at"}) == old.model_dump(
        exclude={"result_expires_at"}), "the two fixtures are the same success"


def test_only_a_success_with_a_result_carries_an_expiry_after_settlement():
    body = v1fix.load("terminal_success_expiring.json")
    for name in ("terminal_cancelled.json", "terminal_platform_error.json",
                 "terminal_unknown_usage.json"):
        with pytest.raises(pydantic.ValidationError):
            v1.TerminalOutcome.model_validate({**v1fix.load(name),
                                               "result_expires_at": body["result_expires_at"]})
    for expires in (body["settled_at"], "2026-09-20T12:00:08Z"):
        with pytest.raises(pydantic.ValidationError):
            v1.TerminalOutcome.model_validate({**body, "result_expires_at": expires})


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_every_read_outcome_is_the_committed_cross_language_table():
    """Oracle: an expired result rendered available (the equality instant), an invented
    expiry, or a held/failed outcome served as a result all disagree with a row."""
    table = v2fix.load("result_read_cases.json")
    for row in table:
        outcome = v1.TerminalOutcome.model_validate(row["outcome"]) if "outcome" in row else None
        assert lc.read_outcome(outcome, _at(row["now"])).value == row["expected"], row["name"]
    assert {row["expected"] for row in table} == {r.value for r in lc.ReadOutcome}


# --- TypeScript parity ---------------------------------------------------------------------
# Read when a parity test runs, not at import: the mutation runner copies only this package,
# and its lifecycle cases must still collect there.
def _ts() -> str:
    return LIFECYCLE_TS.read_text()


SHARED = {"UPLOAD_STATES": lc.UploadState, "READINESS_STATES": lc.ReadinessState,
          "CONTENT_KINDS": lc.ContentKind, "CONTENT_LOCATIONS": lc.ContentLocation,
          "CONTENT_ORIGINS": lc.ContentOrigin, "LIFECYCLE_STATES": lc.LifecycleState,
          "LIFECYCLE_REFUSALS": lc.LifecycleRefusal, "READ_OUTCOMES": lc.ReadOutcome}


@pytest.mark.parametrize("name", sorted(SHARED))
def test_the_console_declares_the_same_lifecycle_vocabulary(name):
    assert ts_string_array(_ts(), name) == [member.value for member in SHARED[name]]


def test_every_lifecycle_vocabulary_is_compared():
    declared = {value for value in vars(lc).values() if isinstance(value, type)
                and issubclass(value, enum.StrEnum) and value.__module__ == lc.__name__}
    assert declared | {lc.UploadState} == set(SHARED.values()), sorted(t.__name__ for t in declared)


def test_the_console_refusal_codes_are_the_table():
    """The TS map, parsed literally, equals the committed table row for row."""
    body = re.search(r"export const LIFECYCLE_REFUSAL_CODES[^=]*=\s*Object\.freeze\(\{(.*?)\}\)",
                     _ts(), re.S)
    assert body, "LIFECYCLE_REFUSAL_CODES not found"
    parsed = dict(re.findall(r'(\w+):\s*"([a-z_]+)"', body.group(1)))
    assert parsed == {row["reason"]: row["code"] for row in v2fix.load("lifecycle_refusals.json")}
