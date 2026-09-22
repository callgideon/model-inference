#!/usr/bin/env python3
"""CREDIT-GRANT and CREDIT-UNITS, record level (F2P item 5).

The one-time individual grant, and the separation of the legacy USD regime from the
CREDIT one: the grant key is the identity and nothing else, and no total, DTO or
projection can mix the two units or invent a conversion.

    uv run --frozen pytest -q tests/contracts/v2/test_credit_v2.py
"""
from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from infrx.contracts import records as v1
from infrx.contracts.v2 import fixtures as v2fix, money_units as mu, records as v2

IDS = v2fix.IDS
GRANT = v2fix.load("signup_grant.json")
ENTRY = v2fix.load("credit_ledger_signup.json")
WALLET = v2.WalletRef.model_validate(v2fix.load("wallet_consumer.json"))
PROVIDER_WALLET = v2.WalletRef.model_validate(v2fix.load("wallet_provider_dev.json"))
NOW = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.timezone.utc)


# --- the grant ---------------------------------------------------------------
def test_the_grant_is_exactly_ten_thousand_credit():
    grant = v2.SignupGrant.model_validate(GRANT)
    assert grant.amount == v2.INITIAL_SIGNUP_GRANT == mu.Credit("10000.00000000")
    assert str(grant.amount) == "10000.00000000"
    assert isinstance(grant.amount, mu.Credit)
    assert grant.entitlement == v2.INITIAL_SIGNUP_ENTITLEMENT == "initial_signup_grant"
    for bad in ("9999.99999999", "10000.00000001", "0.00000000", "-10000.00000000"):
        with pytest.raises(ValidationError):
            v2.SignupGrant.model_validate(dict(GRANT, amount=bad))


def test_the_key_is_the_individual_and_the_entitlement_only():
    """CREDIT-IDENTITY: a campaign bump, another organization or a provider
    membership must not make the same human eligible again, so none of them is in
    the key the unique index is on."""
    grant = v2.SignupGrant.model_validate(GRANT)
    assert grant.key == (IDS.consumer_user, "initial_signup_grant")
    for variation in ({"campaign_version": "relaunch_2027"}, {"campaign_version": ""},
                      {"verification_evidence_ref": "other/evidence"},
                      {"ledger_operation_id": IDS.access_grant}):
        assert v2.SignupGrant.model_validate(dict(GRANT, **variation)).key == grant.key
    # a different user is a different key, and that is the only thing that is
    assert v2.SignupGrant.model_validate(dict(GRANT, user_id=IDS.other_user)).key != grant.key
    assert "campaign_version" not in v2.SignupGrant.model_fields["entitlement"].metadata


def test_an_unverified_grant_is_refused():
    for blank in ("", "   "):
        with pytest.raises(ValidationError, match="verification evidence"):
            v2.SignupGrant.model_validate(dict(GRANT, verification_evidence_ref=blank))


def test_issue_signup_grant_produces_the_grant_and_its_ledger_entry_together():
    grant, entry = v2.issue_signup_grant(
        wallet=WALLET, user_id=IDS.consumer_user, verification_evidence_ref="evidence/1",
        ledger_operation_id=IDS.signup_operation, granted_at=NOW)
    assert grant.wallet_id == entry.wallet_id == WALLET.wallet_id
    assert grant.ledger_operation_id == entry.operation_id == IDS.signup_operation
    assert entry.kind is v2.LedgerEntryKind.signup_grant
    assert entry.amount == v2.INITIAL_SIGNUP_GRANT
    assert entry.actor == v1.PLATFORM_ACTOR
    assert entry.request_id is None


def test_a_provider_wallet_or_a_foreign_wallet_cannot_receive_the_grant():
    with pytest.raises(ValueError, match="no signup entitlement"):
        v2.issue_signup_grant(wallet=PROVIDER_WALLET, user_id=IDS.consumer_user,
                              verification_evidence_ref="evidence",
                              ledger_operation_id=IDS.signup_operation, granted_at=NOW)
    with pytest.raises(ValueError, match="own wallet"):
        v2.issue_signup_grant(wallet=WALLET, user_id=IDS.other_user,
                              verification_evidence_ref="evidence",
                              ledger_operation_id=IDS.signup_operation, granted_at=NOW)


# --- the ledger vocabulary ---------------------------------------------------
def test_the_ledger_kinds_are_closed_and_contain_no_transfer():
    kinds = {kind.value for kind in v2.LedgerEntryKind}
    assert kinds == {"signup_grant", "operator_allocation", "operator_adjustment",
                     "inference_debit"}
    assert not any("transfer" in kind or "convert" in kind for kind in kinds)
    with pytest.raises(ValidationError):
        v2.CreditLedgerEntry.model_validate(dict(ENTRY, kind="transfer"))


@pytest.mark.parametrize("broken", [
    {"wallet_kind": "provider_dev"},                     # provider wallets get no grant
    {"amount": "5000.00000000"},                         # not the promotional amount
    {"request_id": "f0000001-0000-4000-8000-000000000001"},   # not a request settlement
    {"actor": "  "},                                     # unattributed movement
    {"unit": "USD"},                                     # wrong denomination
])
def test_a_signup_ledger_entry_refuses_every_incoherent_shape(broken):
    with pytest.raises(ValidationError):
        v2.CreditLedgerEntry.model_validate(dict(ENTRY, **broken))


def test_an_operator_allocation_funds_only_a_provider_wallet_and_a_debit_is_negative():
    allocation = dict(ENTRY, kind="operator_allocation", wallet_kind="provider_dev",
                      wallet_id=IDS.provider_dev_wallet, amount="250.00000000",
                      actor="ops@platform")
    assert v2.CreditLedgerEntry.model_validate(allocation).amount == mu.Credit("250.00000000")
    for broken in ({"wallet_kind": "consumer"}, {"amount": "0.00000000"},
                   {"amount": "-1.00000000"}):
        with pytest.raises(ValidationError):
            v2.CreditLedgerEntry.model_validate(dict(allocation, **broken))
    debit = dict(ENTRY, kind="inference_debit", amount="-9.97600000",
                 request_id=IDS.request, actor="platform")
    assert v2.CreditLedgerEntry.model_validate(debit).amount.is_negative
    with pytest.raises(ValidationError):
        v2.CreditLedgerEntry.model_validate(dict(debit, amount="9.97600000"))
    with pytest.raises(ValidationError):
        v2.CreditLedgerEntry.model_validate({k: v for k, v in debit.items()
                                             if k != "request_id"})


# --- the legacy regime, kept separate ----------------------------------------
def test_a_nonzero_legacy_usd_balance_is_a_rollout_hold():
    statement = v2fix.load("legacy_usd_statement.json")
    assert v2.LegacyUsdStatement.model_validate(statement).rollout_hold is True
    with pytest.raises(ValidationError, match="rollout hold"):
        v2.LegacyUsdStatement.model_validate(dict(statement, rollout_hold=False))
    zero = v2.LegacyUsdStatement.model_validate(
        dict(statement, balance="0.00000000", rollout_hold=False))
    assert zero.balance.is_zero and isinstance(zero.balance, mu.Usd)


def test_there_is_no_upgrade_from_a_v1_usd_price_snapshot():
    from infrx.contracts import fixtures as v1fix
    snapshot = v1.PriceSnapshot.model_validate(v1fix.load("price_snapshot.json"))
    with pytest.raises(ValueError, match="no conversion to CREDIT"):
        v2.upgrade_v1_price_snapshot_is_refused(snapshot)


def test_a_legacy_usage_row_projects_without_inventing_the_new_fields():
    row = v2.project_v1_usage({
        "request_id": IDS.legacy_request, "cost_usd": "0.01414000", "prompt_tokens": 23500,
        "completion_tokens": 480, "price_version": "pv_2026_09_01", "settled_at": NOW,
    }, org_id=IDS.consumer_org)
    assert row.accounting_regime is v2.AccountingRegime.legacy_usd and row.unit == "USD"
    assert row.rate_card_version is None and row.serving_version_id is None
    assert row.deployment_revision_id is None
    assert str(row.amount()) == "0.01414000" and isinstance(row.amount(), mu.Usd)
    # an old row with no recorded price version keeps None rather than a guess
    bare = v2.project_v1_usage({"request_id": IDS.legacy_request, "cost_usd": "0",
                                "prompt_tokens": 0, "completion_tokens": 0, "settled_at": NOW},
                               org_id=IDS.consumer_org)
    assert bare.price_version is None
    # and a CREDIT-regime row is not "projected" by this path at all
    with pytest.raises(ValueError, match="pre-cutover"):
        v2.project_v1_usage({"request_id": IDS.request, "accounting_regime": "credit",
                             "cost_usd": "0", "prompt_tokens": 0, "completion_tokens": 0,
                             "settled_at": NOW}, org_id=IDS.consumer_org)


def test_a_usage_row_declares_its_regime_and_the_unit_must_match():
    credit_row = v2fix.load("usage_credit.json")
    legacy_row = v2fix.load("usage_legacy_usd.json")
    assert v2.UsageRecordV2.model_validate(credit_row).unit == "CREDIT"
    for broken in ({"unit": "USD"}, {"accounting_regime": "legacy_usd"},
                   {"charged_amount": "9.976"}, {"charged_amount": "1e3"},
                   {"price_version": "pv_2026_09_01"}):
        with pytest.raises(ValidationError):
            v2.UsageRecordV2.model_validate(dict(credit_row, **broken))
    for broken in ({"unit": "CREDIT"}, {"accounting_regime": "credit"},
                   {"rate_card_version": "rc_x"},
                   {"serving_version_id": IDS.serving_version}):
        with pytest.raises(ValidationError):
            v2.UsageRecordV2.model_validate(dict(legacy_row, **broken))


def test_a_mixed_history_answers_per_unit_and_has_no_combined_total():
    history = v2.UsageHistory.model_validate(v2fix.load("usage_history_mixed.json"))
    assert history.totals() == {"CREDIT": "9.97600000", "USD": "0.01414000"}
    assert "total" not in {name for name in dir(history) if not name.startswith("_")} - \
        {"totals"}
    # an empty history answers nothing rather than a zero in an unstated unit
    assert v2.UsageHistory(org_id=IDS.consumer_org).totals() == {}


def test_a_balance_derives_available_and_keeps_the_legacy_statement_beside_it():
    balance = v2.BalanceV2.model_validate(v2fix.load("balance.json"))
    assert balance.available == balance.ledger_total - balance.reserved_total
    assert str(balance.available) == "9989.98560000"
    assert balance.legacy_usd is not None
    assert isinstance(balance.legacy_usd.balance, mu.Usd)
    with pytest.raises(TypeError):
        balance.ledger_total + balance.legacy_usd.balance
    body = v2fix.load("balance.json")
    with pytest.raises(ValidationError, match="available is ledger_total"):
        v2.BalanceV2.model_validate(dict(body, available="10000.00000000"))
    with pytest.raises(ValidationError):
        v2.BalanceV2.model_validate(dict(body, reserved_total="20000.00000000",
                                         available="-10000.00000000"))


def test_an_external_provider_budget_is_its_own_unit():
    budget = v2.ProviderBudget.model_validate(v2fix.load("provider_budget.json"))
    assert budget.unit == "PROVIDER_USD"
    assert isinstance(budget.limit, mu.ProviderUsd)
    with pytest.raises(TypeError):
        budget.limit + mu.Credit("1")
    with pytest.raises(TypeError):
        budget.limit + mu.Usd("1")
