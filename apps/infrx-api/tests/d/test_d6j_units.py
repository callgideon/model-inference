#!/usr/bin/env python3
"""D6J: the Python half of `PgLabConsentStore`, with NO database - what it sends to the RPCs of
`0031_lab_consent.sql`. `code_mutants_d6j.py`'s Python list runs here; the SQL is
`test_d6j_consent.py`.

    uv run --frozen pytest -q tests/d/test_d6j_units.py
"""
from __future__ import annotations

from infrx.contracts import errors
from infrx.state.lab_consent import PgLabConsentStore

from .test_adapter_units import _Conn, _db_error, _refused
from .test_d7_units import NEMO, _ok, _sent

COST = {"unit": "PROVIDER_USD", "value": "3.00000000"}


def _store(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgLabConsentStore(connect), conn


def test_calls__carry_the_callers_provider_and_only_the_fields_given() -> None:
    store, conn = _store(*({},) * 9)
    _ok(store.put_budget(provider_org_id=NEMO, payer_ref="p", limit="9", actor="a", reason="r"))
    _ok(store.budget("p", provider_org_id=NEMO))
    _ok(store.prepare("x", provider_org_id=NEMO, actor="a"))
    _ok(store.begin_submit("e", provider_org_id=NEMO))
    _ok(store.accepted("e", "b1", provider_org_id=NEMO))
    _ok(store.ambiguous("e", "timeout", provider_org_id=NEMO))
    _ok(store.finish("e", "completed", provider_org_id=NEMO, cost=COST))
    _ok(store.submission("e", provider_org_id=NEMO))
    _ok(store.finish("e", "failed", provider_org_id=NEMO))
    move = {"provider_org_id": NEMO, "external_run_id": "e"}
    assert [_sent(conn, n) for n in range(9)] == [
        ("lab_put_budget", {"provider_org_id": NEMO, "payer_ref": "p", "limit": "9",
                            "actor": "a", "reason": "r"}),
        ("lab_budget", {"provider_org_id": NEMO, "payer_ref": "p"}),
        ("lab_prepare_submission", {"provider_org_id": NEMO, "external_run_ref": "x",
                                    "actor": "a"}),
        ("lab_submission_transition", {**move, "state": "submitting"}),
        ("lab_submission_transition", {**move, "state": "submitted",
                                       "external_batch_id": "b1"}),
        ("lab_submission_transition", {**move, "state": "ambiguous", "reason": "timeout"}),
        ("lab_submission_transition", {**move, "state": "completed", "cost": COST}),
        ("lab_submission", move),
        ("lab_submission_transition", {**move, "state": "failed"})]


def test_refusals__are_typed_so_a_duplicate_submit_is_never_retried_blind() -> None:
    store, _ = _store(
        _db_error("P0001", "ambiguous_submission: submit:e is submitting"),
        _db_error("P0001", "consent_missing: a snapshotted grant changed"),
        _db_error("P0001", "budget_exceeded: the reservation exceeds the payer's budget"),
        _db_error("55000", "maintenance: lab_submission is not enabled"))
    _refused(errors.AmbiguousSubmission, store.begin_submit("e", provider_org_id=NEMO))
    _refused(errors.ConsentMissing, store.begin_submit("e", provider_org_id=NEMO))
    _refused(errors.BudgetExceeded, store.prepare("x", provider_org_id=NEMO, actor="a"))
    _refused(errors.DependencyUnavailable, store.prepare("x", provider_org_id=NEMO, actor="a"))
