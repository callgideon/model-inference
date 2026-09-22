#!/usr/bin/env python3
"""CREDIT-IDENTITY / SPLIT-CONTRACT, record level: credential -> wallet (F2P item 3).

The conformance suite covers the resolution behaviour a track must reproduce; this
covers the record validators that make the resolution the *only* path — ownership
exclusive-or, audience/identity coherence, and the absence of any wallet field a
caller could fill in.

    uv run --frozen pytest -q tests/contracts/v2/test_identity_v2.py
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrx.contracts import errors, records as v1
from infrx.contracts.v2 import fixtures as v2fix, money_units as mu, ports as v2ports, records as v2

IDS = v2fix.IDS
CONSUMER = v2fix.load("wallet_consumer.json")
PROVIDER = v2fix.load("wallet_provider_dev.json")
AUTH = v2fix.load("auth_context_consumer.json")
PROVIDER_AUTH = v2fix.load("auth_context_provider_dev.json")


def test_a_consumer_wallet_is_owned_by_a_user_and_bound_to_a_personal_org():
    wallet = v2.WalletRef.model_validate(CONSUMER)
    assert wallet.kind is v2.WalletKind.consumer and wallet.unit == mu.CREDIT
    assert wallet.owner_user_id == IDS.consumer_user
    assert wallet.personal_org_id == IDS.consumer_org
    assert wallet.owner_provider_org_id is None
    assert wallet.has_signup_entitlement is True


@pytest.mark.parametrize("broken,why", [
    ({"owner_user_id": None}, "a consumer wallet with no individual owner"),
    ({"personal_org_id": None}, "a consumer wallet with no personal-org binding"),
    ({"owner_provider_org_id": IDS.provider_org}, "a consumer wallet owned by a provider too"),
    ({"reserved_total": "20000.00000000"}, "reservations exceeding the ledger total"),
    ({"ledger_total": "-1.00000000"}, "a negative wallet total"),
    ({"unit": "USD"}, "a wallet denominated in dollars"),
])
def test_a_consumer_wallet_refuses_broken_ownership_or_totals(broken, why):
    with pytest.raises(ValidationError):
        v2.WalletRef.model_validate(dict(CONSUMER, **broken))
    assert why


@pytest.mark.parametrize("broken", [
    {"owner_provider_org_id": None},
    {"owner_user_id": IDS.consumer_user},
    {"personal_org_id": IDS.consumer_org},
])
def test_a_provider_dev_wallet_has_no_individual_owner(broken):
    with pytest.raises(ValidationError):
        v2.WalletRef.model_validate(dict(PROVIDER, **broken))


def test_a_provider_dev_wallet_starts_at_zero_with_no_entitlement():
    wallet = v2.WalletRef.model_validate(PROVIDER)
    assert wallet.ledger_total.is_zero and wallet.reserved_total.is_zero
    assert wallet.available == mu.ZERO_CREDIT
    assert wallet.has_signup_entitlement is False


def test_available_is_derived_and_typed_as_credit():
    wallet = v2.WalletRef.model_validate(CONSUMER)
    assert isinstance(wallet.available, mu.Credit)
    assert str(wallet.available) == "9989.98560000"
    with pytest.raises(TypeError):
        wallet.available + mu.Usd("1")


def test_the_auth_context_has_no_wallet_field_and_refuses_one():
    """F2P item 3: "request fields can never select a wallet". `extra="forbid"` is
    what makes that structural rather than a check somebody must remember."""
    assert not {field for field in v2.AuthContextV2.model_fields if "wallet" in field}
    for forged in ({"wallet_id": IDS.other_wallet}, {"wallet": "mine"},
                   {"owner_user_id": IDS.other_user}, {"ledger_total": "1000000.00000000"}):
        with pytest.raises(ValidationError):
            v2.AuthContextV2.model_validate(dict(AUTH, **forged))


def test_the_audience_vocabulary_is_closed_and_carries_its_own_identity():
    assert {a.value for a in v2.CredentialAudience} == {"consumer", "provider_dev", "operator"}
    assert {k.value for k in v2.WalletKind} == {"consumer", "provider_dev"}
    with pytest.raises(ValidationError):
        v2.AuthContextV2.model_validate(dict(AUTH, audience="provider"))
    # a consumer credential with a provider or endpoint scope is incoherent
    for forged in ({"provider_org_id": IDS.provider_org}, {"endpoint_id": IDS.dev_endpoint}):
        with pytest.raises(ValidationError):
            v2.AuthContextV2.model_validate(dict(AUTH, **forged))
    # ... and one with no user has no individual behind it
    with pytest.raises(ValidationError):
        v2.AuthContextV2.model_validate({k: v for k, v in AUTH.items() if k != "user_id"})


def test_a_provider_dev_credential_is_scoped_to_one_provider_and_one_endpoint():
    auth = v2.AuthContextV2.model_validate(PROVIDER_AUTH)
    assert auth.provider_org_id == IDS.provider_org and auth.endpoint_id == IDS.dev_endpoint
    for missing in ("provider_org_id", "endpoint_id"):
        with pytest.raises(ValidationError):
            v2.AuthContextV2.model_validate(
                {k: v for k, v in PROVIDER_AUTH.items() if k != missing})


def test_an_operator_credential_carries_no_provider_or_endpoint_scope():
    base = dict(AUTH, audience="operator", role="operator", principal="ops@platform")
    operator = v2.AuthContextV2.model_validate(base)
    assert operator.is_operator is True
    with pytest.raises(ValidationError):
        v2.AuthContextV2.model_validate(dict(base, endpoint_id=IDS.dev_endpoint))


def test_resolve_wallet_reads_only_the_auth_context_and_the_trusted_row():
    """The signature is the contract: there is no request parameter at all."""
    import inspect
    parameters = list(inspect.signature(v2ports.resolve_wallet).parameters)
    assert parameters == ["auth", "wallet"], parameters
    auth = v2.AuthContextV2.model_validate(AUTH)
    wallet = v2.WalletRef.model_validate(CONSUMER)
    assert v2ports.resolve_wallet(auth, wallet) is wallet
    with pytest.raises(errors.NotFound):
        v2ports.resolve_wallet(auth, None)
    with pytest.raises(errors.Forbidden):
        v2ports.resolve_wallet(auth, v2.WalletRef.model_validate(PROVIDER))


def test_a_provider_credential_cannot_resolve_a_consumer_wallet_or_a_rivals():
    auth = v2.AuthContextV2.model_validate(PROVIDER_AUTH)
    rival = v2.WalletRef.model_validate(
        dict(PROVIDER, owner_provider_org_id=IDS.rival_provider_org))
    for wallet in (v2.WalletRef.model_validate(CONSUMER), rival):
        with pytest.raises(errors.Forbidden):
            v2ports.resolve_wallet(auth, wallet)


def test_the_v1_auth_context_is_untouched():
    """F-BASE: v2 is additive. v1's AuthContext keeps exactly its own fields."""
    assert set(v1.AuthContext.model_fields) == {
        "schema_version", "org_id", "key_id", "principal", "role", "entitlement_version",
        "legacy_key"}
    assert "audience" not in v1.AuthContext.model_fields
