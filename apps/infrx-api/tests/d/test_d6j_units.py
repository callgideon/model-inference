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


# --- SR-J2-1: PgJudgeLedger (0036) --------------------------------------------------------
RUN = {"run_id": "r", "provider_org_id": NEMO, "payer_ref": "p", "grant_id": "g",
       "grant_version": 2, "sample_ids": ["s1", "s2"], "media_ids": ["s1"],
       "sent_sample_ids": ["s1"], "price_version": "v", "reserved": "40.00000000",
       "actual": None, "state": "submitted", "submit_key": "submit:r", "external_id": "b"}


class _Result:                              # J2's `Rejected`: no scores, so not accepted
    def __init__(self) -> None:
        self.run_id, self.sample_id, self.rubric_version = "r", "s1", 3

    def __iter__(self):
        return iter({"reason": "schema"}.items())


def test_judge__sends_the_ports_fields_and_types_the_answers() -> None:
    from infrx.contracts.v2.money_units import ProviderUsd
    from infrx.state.lab_consent import Consent, PgJudgeLedger
    conn = _Conn([RUN, {"run": RUN, "created": False}, RUN, {"inserted": 1},
                  {"refused": "budget_exceeded", "run": RUN}, {**RUN, "actual": "1.00000000",
                                                               "state": "completed"}, None])

    async def connect():
        return conn
    ledger = PgJudgeLedger(connect)
    run = _ok(ledger.reserve(run_id="r", provider_org_id=NEMO, payer_ref="p",
                             consent=Consent("g", 2), sample_ids=["s1", "s2"],
                             media_ids=frozenset({"s1"}), price_version="v",
                             max_cost=ProviderUsd("40")))
    assert (run.consent, str(run.reserved), run.sent_ids, run.media_ids) == \
        (Consent("g", 2), "40.00000000", ("s1",), frozenset({"s1"}))
    assert _ok(ledger.begin_submit("r"))[1] is False
    _ok(ledger.record_sent("r", ["s1"]))
    assert _ok(ledger.record_results("r", [_Result()])) == 1
    _refused(errors.BudgetExceeded, ledger.settle("r", ProviderUsd("41")))
    assert str(_ok(ledger.settle("r", ProviderUsd("1"))).actual) == "1.00000000"
    assert _ok(ledger.run("x")) is None
    assert [_sent(conn, n) for n in range(7)] == [
        ("lab_judge_reserve", {"run_id": "r", "provider_org_id": NEMO, "payer_ref": "p",
                               "grant_id": "g", "grant_version": 2,
                               "sample_ids": ["s1", "s2"], "media_ids": ["s1"],
                               "price_version": "v", "max_cost": "40.00000000"}),
        ("lab_judge_begin_submit", {"run_id": "r"}),
        ("lab_judge_record_sent", {"run_id": "r", "sample_ids": ["s1"]}),
        ("lab_judge_record_results", {"run_id": "r", "results": [
            {"sample_id": "s1", "rubric_version": 3, "accepted": False,
             "result": {"reason": "schema"}}]}),
        ("lab_judge_settle", {"run_id": "r", "actual": "41.00000000"}),
        ("lab_judge_settle", {"run_id": "r", "actual": "1.00000000"}),
        ("lab_judge_run", {"run_id": "x"})]
