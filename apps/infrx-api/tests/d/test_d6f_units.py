#!/usr/bin/env python3
"""D6F: the Python half of the feedback service, with NO database - what `PgFeedbackService`
decides before and after its one call: the server-derived provenance it sends, the typed
refusals it gives without calling, the one visibility rule on every answer (replays
included), and the disabled flag as `DependencyUnavailable`. `code_mutants_d6f.py`'s Python
list runs here; the SQL is `test_d6f_feedback.py`.

    uv run --frozen pytest -q tests/d/test_d6f_units.py
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.records import FeedbackChannel, FeedbackName, Role
from infrx.contracts.wire import PLATFORM_ACTOR
from infrx.state.feedback import PgFeedbackService

from .test_adapter_units import _Conn, _db_error, _refused

REQUEST = b.request(SimpleNamespace(clock=FakeClock(), ids=SequentialIds()))
IDEM = b.idem(REQUEST, "fb-1", operation="feedback")
OPERATOR = b.auth(role=Role.operator)


def _row(**over) -> dict:
    return {"feedback_id": "fb_" + "a" * 26, "request_id": REQUEST.request_id,
            "org_id": b.ORG_A, "author_principal": b.KEY_A, "author_role": "customer",
            "channel": "api", "name": "rating", "value": 4, "comment": None,
            "calibration_set": False, "rubric_version": None, "by_operator": False,
            "created_at": "2026-09-27T00:00:00Z", **over}


LABEL = _row(author_role="operator", channel="console", name="calibration_label",
             value="correct", calibration_set=True, rubric_version=3, by_operator=True)


def _service(*answers, channel=FeedbackChannel.api):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgFeedbackService(connect, channel=channel), conn


def _args(conn) -> dict:
    ((sql, (params,)),) = conn.sent
    return params.obj


def test_accept__sends_server_derived_provenance_and_the_signal_only() -> None:
    service, conn = _service(_row(by_operator=True), channel=FeedbackChannel.console)
    asyncio.run(service.accept(OPERATOR, REQUEST.request_id,
                               b.feedback(FeedbackName.rating, 4), IDEM))
    args = _args(conn)
    assert "infrx.accept_feedback" in conn.sent[0][0]
    assert args["org_id"] == b.ORG_A and args["principal"] == OPERATOR.principal
    assert args["by_operator"] is True and args["channel"] == "console"
    assert args["body"] == {"name": "rating", "value": 4}, args["body"]
    assert args["feedback_id"].startswith("fb_") and args["request_id"] == REQUEST.request_id
    assert args["idem"]["key"] == "fb-1"


def test_accept__refuses_forged_fields_and_foreign_scopes_without_calling() -> None:
    for body, idem, cls in (
        ({**b.feedback(), "author_role": "operator"}, IDEM, errors.InvalidRequest),
        ({**b.feedback(), "by_operator": True}, IDEM, errors.InvalidRequest),
        ({}, IDEM, errors.InvalidRequest),
        ([b.feedback()], IDEM, errors.InvalidRequest),
        (b.feedback(FeedbackName.rating, 1.0), IDEM, errors.InvalidRequest),
        (b.feedback(), IDEM.model_copy(update={"org_id": b.ORG_B}), errors.Forbidden),
        (b.feedback(), b.idem(REQUEST, None, operation="feedback"), errors.InvalidRequest),
    ):
        service, conn = _service(_row())
        _refused(cls, service.accept(b.auth(), REQUEST.request_id, body, idem))
        assert conn.sent == [], f"{body} reached the database"


def test_accept__an_operator_row_reads_platform_to_a_customer() -> None:
    service, _ = _service(_row(by_operator=True, author_principal="ops@infrx"))
    got = asyncio.run(service.accept(b.auth(), REQUEST.request_id, b.feedback(), IDEM))
    assert got.author_principal == PLATFORM_ACTOR, got
    service, _ = _service(_row(by_operator=True, author_principal="ops@infrx"))
    got = asyncio.run(service.accept(OPERATOR, REQUEST.request_id, b.feedback(), IDEM))
    assert got.author_principal == "ops@infrx", "an operator sees the real principal"


def test_label__operator_only_bounded_and_sends_the_label() -> None:
    idem = b.idem(REQUEST, "cal-1", operation="calibration.label")
    for auth, label, version, comment, cls in (
        (b.auth(), "correct", 3, None, errors.Forbidden),
        (OPERATOR, "golden", 3, None, errors.InvalidRequest),
        (OPERATOR, "correct", True, None, errors.InvalidRequest),
        (OPERATOR, "correct", 1001, None, errors.InvalidRequest),
        (OPERATOR, "correct", 3, "c" * 4001, errors.InvalidRequest),
    ):
        service, conn = _service(LABEL)
        _refused(cls, service.label_calibration(auth, REQUEST.request_id, label, version, idem,
                                                comment=comment))
        assert conn.sent == []
    service, conn = _service(LABEL)
    _refused(errors.InvalidRequest, service.label_calibration(
        OPERATOR, REQUEST.request_id, "correct", 3,
        b.idem(REQUEST, None, operation="calibration.label")))
    assert conn.sent == []
    got = asyncio.run(service.label_calibration(OPERATOR, REQUEST.request_id, "correct", 3,
                                                idem))
    args = _args(conn)
    assert "infrx.label_calibration" in conn.sent[0][0]
    assert (args["label"], args["rubric_version"], args["is_operator"]) == ("correct", 3, True)
    assert args["principal"] == OPERATOR.principal and got.calibration_set


def test_lists__owned_is_the_callers_org_and_labels_are_operator_only() -> None:
    service, conn = _service([_row(), LABEL])
    got = asyncio.run(service.list_owned(b.auth(), REQUEST.request_id))
    assert _args(conn) == {"request_id": REQUEST.request_id, "org_id": b.ORG_A}
    assert [r.name for r in got] == [FeedbackName.rating], "a label reached list_owned"
    service, conn = _service([LABEL])
    _refused(errors.Forbidden, service.list_calibration(b.auth(), REQUEST.request_id))
    assert conn.sent == []
    got = asyncio.run(service.list_calibration(OPERATOR, REQUEST.request_id))
    assert _args(conn) == {"request_id": REQUEST.request_id, "calibration": True}
    assert [r.calibration_set for r in got] == [True]


def test_refusals__the_disabled_flag_and_sql_codes_are_typed() -> None:
    for error, cls in ((_db_error("0A000", "feedback is not enabled"),
                        errors.DependencyUnavailable),
                       (_db_error("P0001", "not_found: no request"), errors.NotFound),
                       (_db_error("P0001", "idempotency_conflict: same key"),
                        errors.IdempotencyConflict)):
        service, _ = _service(error)
        _refused(cls, service.accept(b.auth(), REQUEST.request_id, b.feedback(), IDEM))
