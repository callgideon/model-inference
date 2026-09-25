#!/usr/bin/env python3
"""D5: the Python half of the settlement, with NO database - what the adapter (not the SQL)
decides: what `complete`/`complete_credit` send (the proposal, the regime, the store's own
TTLs), that a refusal after a committed terminalization is raised as its type (R39), how
the `SettlementV2` is built from the store's rows (exactly when settled, the recorded
charge), the cancel cause, `load_work_credit`'s WorkV2 (a legacy job refused), and the 24 h
release reported in `released`. `tests/d/code_mutants_d5.py` runs here (no Docker); the SQL
is `test_settle.py`.
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import TerminalCause, TerminalOutcome, Usage
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.records import SettlementV2

from .test_adapter_units import _refused, _store
from .test_lease_units import LEASE, OUTCOME, REFUSED, _args, _work_doc

JOB = LEASE.job_id
SETTLED = {**OUTCOME, "state": "succeeded", "cause": "completed", "result_ref":
           f"infrx-result:{JOB}", "settlement_state": "settled",
           "usage": Usage.of(1200, 340).model_dump(mode="json")}
PINS = v2fix.BUILDERS["admission_pins.json"]().model_dump(mode="json")
CARD = v2fix.BUILDERS["rate_card_marlin.json"]().model_dump(mode="json")
POLICY = v2fix.BUILDERS["data_access_policy.json"]().model_dump(mode="json")


def _ok(coro):
    """The call's answer; any exception is the assertion it stands for (R40: a code mutant
    must die on an assertion of the named case, never on a crash)."""
    try:
        return asyncio.run(coro)
    except Exception as failed:
        raise AssertionError(f"{type(failed).__name__}: {failed}") from None


def _doc(outcome: dict, **extra) -> dict:
    return {"request_id": JOB, "wallet_id": v2fix.IDS.consumer_wallet, "pins": PINS,
            "charged_credits": None, "outcome": outcome, **extra}


def test_complete__sends_the_proposal_the_regime_and_the_stores_ttls() -> None:
    """The proposal as the record dumps it, the legacy regime, and the store's own
    retuned result and tombstone TTLs (never the defaults); the answer is the store's
    committed outcome, its usage included."""
    limits = DEFAULTS.replace(result_ttl_s=77.0, idempotency_ttl_s=88.0, lease_ttl_s=9.0)
    store, conn = _store(_doc(SETTLED), limits=limits)
    proposal = TerminalOutcome(**{**SETTLED, "settlement_state": "released_free"})
    settled = _ok(store.complete(LEASE, proposal))
    sent = _args(conn)
    assert sent["outcome"] == proposal.model_dump(mode="json"), sent["outcome"]
    assert sent["regime"] == "legacy_usd", sent["regime"]
    assert (sent["limits"]["result_ttl_s"], sent["limits"]["idempotency_ttl_s"],
            sent["limits"]["lease_ttl_s"]) == (77.0, 88.0, 9.0), sent["limits"]
    assert settled == TerminalOutcome(**SETTLED), settled
    assert settled.usage == Usage.of(1200, 340), settled.usage


def test_complete__a_committed_refusal_is_raised_as_its_type() -> None:
    """R39: an R29 terminalization commits and comes back as data; `complete` and
    `complete_credit` raise it as `AlreadyTerminal`."""
    store, _ = _store(REFUSED, REFUSED)
    _refused(errors.AlreadyTerminal, store.complete(LEASE, TerminalOutcome(**OUTCOME)))
    _refused(errors.AlreadyTerminal, store.complete_credit(LEASE, TerminalOutcome(**OUTCOME)))


def test_complete_credit__a_settlement_exactly_when_settled_at_the_recorded_charge() -> None:
    """CREDIT: the regime is `credit`; a settled outcome carries the `SettlementV2` built
    from the store's rows - the pins, the wallet and the charge the inference debit
    recorded - and anything else carries none."""
    unsettled = [{**OUTCOME, "state": "failed", "cause": "client_disconnected",
                  "settlement_state": state, "debit": "0.00000000", "reconcile_after": after}
                 for state, after in (("held_unknown", "2026-09-21T12:00:00Z"),
                                      ("released_platform_absorbed", None))]
    store, conn = _store(_doc(SETTLED, charged_credits="0.88800000"),
                         _doc({**OUTCOME, "state": "failed", "cause": "invalid_media"}),
                         *[_doc(o) for o in unsettled])
    outcome, settlement = _ok(store.complete_credit(LEASE, TerminalOutcome(**SETTLED)))
    assert _args(conn)["regime"] == "credit"
    want = SettlementV2.model_validate({
        "request_id": JOB, "wallet_id": v2fix.IDS.consumer_wallet,
        "usage": Usage.of(1200, 340).model_dump(mode="json"), "charged": "0.88800000",
        "rate_card_version": PINS["rate_card_version"],
        "serving_version_id": PINS["serving_version_id"],
        "deployment_revision_id": PINS["deployment_revision_id"],
        "settled_at": outcome.settled_at})
    assert settlement == want, (settlement, want)
    free, none = _ok(store.complete_credit(LEASE, TerminalOutcome(**OUTCOME)))
    assert none is None and free.settlement_state.value == "released_free", (free, none)
    # review N6: held back or platform-absorbed is no settlement either
    for want in unsettled:
        got, none = _ok(store.complete_credit(LEASE, TerminalOutcome(**OUTCOME)))
        assert none is None and got.settlement_state.value == want["settlement_state"], \
            (got, none)


def test_cancel__sends_the_cause_and_defaults_to_the_clients_own() -> None:
    """R21: the cause reaches the store (which refuses anything but the three); none
    given is `client_cancelled`."""
    store, conn = _store(OUTCOME, OUTCOME)
    _ok(store.cancel(b.ORG_A, "job_x", cause="sync_deadline"))
    _ok(store.cancel(b.ORG_A, "job_x"))
    assert (_args(conn, 0)["cause"], _args(conn, 1)["cause"]) == \
        ("sync_deadline", "client_cancelled"), (_args(conn, 0), _args(conn, 1))


def test_fail_preparation__sends_the_lease_the_cause_and_the_limits() -> None:
    """W5 request 3 (0022): one `infrx.fail_preparation` call with the lease, the cause and
    the store's own lease limits; the answer is the committed outcome; a refusal after an
    R29 terminalization is raised as its type."""
    ended = {**OUTCOME, "state": "failed", "cause": "invalid_media"}
    limits = DEFAULTS.replace(unknown_usage_reconcile_s=66.0)
    store, conn = _store({"outcome": ended}, REFUSED, limits=limits)
    answer = _ok(store.fail_preparation(LEASE, TerminalCause.invalid_media))
    assert "infrx.fail_preparation(" in conn.sent[0][0], conn.sent[0][0]
    sent = _args(conn)
    assert (sent["lease"], sent["cause"], sent["limits"]["unknown_usage_reconcile_s"]) == \
        (LEASE.model_dump(mode="json"), "invalid_media", 66.0), sent
    assert answer == TerminalOutcome(**ended), answer
    _refused(errors.AlreadyTerminal,
             store.fail_preparation(LEASE, TerminalCause.preparation_failed))


def test_load_work_credit__the_admitted_work_and_a_legacy_job_refused() -> None:
    """The WorkV2 of the lease's job: its request, the ADMITTED pins, card and policy, the
    wallet, prepared refs and prompt count; a legacy job is `not_found`."""
    request, prepared, doc = _work_doc("credit")
    doc["admission"].update(pins=PINS, rate_card=CARD, wallet_id=v2fix.IDS.consumer_wallet,
                            price_snapshot=None)
    doc["policy"] = POLICY
    _, _, legacy = _work_doc("legacy_usd")
    store, conn = _store(doc, legacy)
    work = _ok(store.load_work_credit(LEASE))
    assert _args(conn)["lease"] == LEASE.model_dump(mode="json")
    assert work.request.request == request and work.prepared_refs == (prepared,), work
    assert work.request.pins.model_dump(mode="json") == PINS, work.request.pins
    assert work.rate_card.model_dump(mode="json") == CARD, work.rate_card
    assert work.request.policy.model_dump(mode="json") == POLICY, work.request.policy
    assert (work.request.wallet_id, work.prompt_tokens) == (v2fix.IDS.consumer_wallet, 1234)
    _refused(errors.NotFound, store.load_work_credit(LEASE))


def test_recover__a_24h_release_is_reported_in_released() -> None:
    """I3B request 5: a release is returned (as its outcome) AND named in `released`; a
    terminalization in the same sweep is not; each sweep reports its own releases."""
    released = {**OUTCOME, "job_id": b.ORG_B, "state": "failed", "cause": "client_disconnected",
                "settlement_state": "released_platform_absorbed"}
    store, _ = _store([{"released": released}, {"outcome": OUTCOME}], [])
    produced = _ok(store.recover())
    assert [o.job_id for o in produced] == [b.ORG_B, JOB], produced
    assert store.released == (b.ORG_B,), store.released
    _ok(store.recover())
    assert store.released == (), "a previous sweep's releases were reported again"


def test_lookup__answers_the_jobs_own_regime_and_sends_the_stores_ttl() -> None:
    """R91: one statement with the store's own tombstone TTL; the answer in the job's OWN
    regime (`Admission` for a legacy job, the pinned `AdmissionV2` for a CREDIT one),
    `replayed`, with its outcome; no mapping answers None."""
    from infrx.contracts.fakes.factories import jobstore_factory
    from infrx.contracts.records import Admission
    from infrx.contracts.v2.records import AdmissionV2
    h = jobstore_factory()
    h.extra["grant"](b.ORG_A, "25")
    request = b.request(h)
    idem = b.idem(request, "k")
    legacy = asyncio.run(h.port.admit(request, idem)).model_dump(mode="json")
    credit = v2fix.BUILDERS["admission.json"]().model_dump(mode="json")
    store, conn = _store({**legacy, "accounting_regime": "legacy_usd", "replayed": True,
                          "outcome": OUTCOME},
                         {**credit, "accounting_regime": "credit", "replayed": True,
                          "outcome": None}, None,
                         limits=DEFAULTS.replace(idempotency_ttl_s=99.0))
    mapped, outcome = _ok(store.lookup(request.org_id, idem))
    assert type(mapped) is Admission and mapped.replayed, mapped
    assert outcome == TerminalOutcome(**OUTCOME), outcome
    sent = _args(conn)
    assert (sent["org_id"], sent["idem"], sent["limits"]) == \
        (request.org_id, idem.model_dump(mode="json"), {"idempotency_ttl_s": 99.0}), sent
    mapped, outcome = _ok(store.lookup(request.org_id, idem))
    assert type(mapped) is AdmissionV2 and mapped.replayed and outcome is None, mapped
    # review N6: the CALLER's organization is sent, never the scope's own (the store
    # refuses a scope naming another org as `forbidden`, R10)
    assert _ok(store.lookup(b.ORG_B, idem)) is None
    assert _args(conn, 2)["org_id"] == b.ORG_B != idem.org_id, _args(conn, 2)


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
