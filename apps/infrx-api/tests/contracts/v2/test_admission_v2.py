#!/usr/bin/env python3
"""CREDIT-RATE, record level: the admission pins (F2P item 4).

The conformance suite proves the behaviour (a rate published after acceptance does
not move the job). This proves the records cannot express the alternative: a rate
card that prices something else cannot be attached, a public request body has no
price or identity field, and `settle` has no way to reach a directory.

    uv run --frozen pytest -q tests/contracts/v2/test_admission_v2.py
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from infrx.contracts import errors, records as v1
from infrx.contracts.v2 import fixtures as v2fix, money_units as mu, ports as v2ports, records as v2

IDS = v2fix.IDS
CARD = v2fix.load("rate_card_marlin.json")
PINS = v2fix.load("admission_pins.json")
ADMISSION = v2fix.load("admission.json")
REQUEST = v2fix.load("normalized_request.json")
SERVING = v2fix.load("serving_revision.json")
PROD = v2fix.load("deployment_revision_public.json")
DEV = v2fix.load("deployment_revision_private_dev.json")


def test_the_pins_name_every_resolved_identity():
    pins = v2.AdmissionPins.model_validate(PINS)
    assert (pins.model_id, pins.deployment_revision_id, pins.serving_version_id) == \
        (IDS.model, IDS.prod_deployment, IDS.serving_version)
    assert pins.rate_card_version == v2fix.RATE_CARD_VERSION
    assert pins.policy_version == v2fix.POLICY_VERSION
    assert pins.requested_model == v2fix.REQUESTED_MODEL
    assert pins.accounting_regime == "credit"
    with pytest.raises(ValidationError):
        v2.AdmissionPins.model_validate(dict(PINS, accounting_regime="legacy_usd"))


def test_an_admission_must_carry_the_card_it_pinned():
    for swap in ({"rate_card_version": "rc_other"},
                 {"deployment_revision_id": IDS.dev_deployment},
                 {"serving_version_id": IDS.model_version}):
        with pytest.raises(ValidationError):
            v2.AdmissionV2.model_validate(
                dict(ADMISSION, rate_card=dict(ADMISSION["rate_card"], **swap)))
    admission = v2.AdmissionV2.model_validate(ADMISSION)
    assert admission.rate_card.rate_card_version == admission.pins.rate_card_version
    assert isinstance(admission.maximum_hold, mu.Credit)
    assert admission.accounting_regime == "credit"


def test_the_work_a_lease_holder_gets_carries_the_admitted_card():
    work = v2.WorkV2.model_validate(v2fix.load("work.json"))
    assert work.rate_card.rate_card_version == work.request.pins.rate_card_version
    body = v2fix.load("work.json")
    with pytest.raises(ValidationError):
        v2.WorkV2.model_validate(
            dict(body, rate_card=dict(body["rate_card"], rate_card_version="rc_other")))


def test_the_public_request_shape_is_the_v1_record_unchanged():
    """"Unchanged public fields" is true by construction: v2 nests v1's record
    rather than restating it, so a public field cannot drift in one place only."""
    assert v2.NormalizedRequestV2.model_fields["request"].annotation is v1.NormalizedRequest
    nested = v1.NormalizedRequest.model_validate(REQUEST["request"])
    assert nested.model_dump(mode="json", exclude_none=True) == REQUEST["request"]
    assert set(v2.NormalizedRequestV2.model_fields) == {
        "schema_version", "request", "pins", "wallet_id", "policy"}


@pytest.mark.parametrize("forged", [
    {"input_rate_per_million": "0.00000001"},
    {"rate_card_version": "rc_free"},
    {"serving_version_id": "d0000003-0000-4000-8000-000000000003"},
    {"deployment_revision_id": "c0000004-0000-4000-8000-000000000004"},
    {"wallet_id": "a0000007-0000-4000-8000-000000000007"},
    {"maximum_hold": "0.00000000"},
    {"price_snapshot": {"currency": "USD"}},
])
def test_a_caller_supplied_price_or_identity_is_refused(forged):
    """R45 extended to v2: none of these exist on the public request shape, so a
    caller cannot supply one and admission cannot echo it."""
    with pytest.raises(ValidationError):
        v1.NormalizedRequest.model_validate(dict(REQUEST["request"], **forged))


def test_pin_admission_has_no_parameter_a_caller_could_route_a_price_through():
    signature = inspect.signature(v2ports.pin_admission)
    assert set(signature.parameters) == {"auth", "requested_model", "deployment", "serving",
                                         "rate_card", "policy"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               for p in signature.parameters.values()), "every input is named at the call site"


def test_an_unresolvable_unpriced_or_mismatched_deployment_is_refused():
    auth = v2.AuthContextV2.model_validate(v2fix.load("auth_context_consumer.json"))
    serving = v2.ServingRevision.model_validate(SERVING)
    prod = v2.DeploymentRevision.model_validate(PROD)
    card = v2.RateCardSnapshot.model_validate(CARD)
    policy = v2.DataAccessPolicyRef.model_validate(v2fix.load("data_access_policy.json"))
    kwargs = dict(auth=auth, requested_model=v2fix.REQUESTED_MODEL, deployment=prod,
                  serving=serving, rate_card=card, policy=policy)
    pins, pinned = v2ports.pin_admission(**kwargs)
    assert pinned is card and pins.rate_card_version == card.rate_card_version
    with pytest.raises(errors.NotFound):
        v2ports.pin_admission(**dict(kwargs, deployment=None))
    with pytest.raises(errors.InvalidRequest):
        v2ports.pin_admission(**dict(kwargs, rate_card=None))
    with pytest.raises(errors.InvalidRequest):
        v2ports.pin_admission(**dict(kwargs, policy=None))
    with pytest.raises(errors.InvalidRequest):
        v2ports.pin_admission(**dict(kwargs, serving=None))
    # a card that prices another revision is refused rather than pinned
    with pytest.raises(errors.InvalidRequest):
        v2ports.pin_admission(**dict(
            kwargs, rate_card=card.model_copy(
                update={"deployment_revision_id": IDS.dev_deployment})))
    # a retired deployment is not found, not "found but unavailable"
    with pytest.raises(errors.NotFound):
        v2ports.pin_admission(**dict(
            kwargs, deployment=prod.model_copy(update={"state": v2.DeploymentState.retired})))


def test_a_consumer_credential_is_refused_a_private_dev_deployment():
    auth = v2.AuthContextV2.model_validate(v2fix.load("auth_context_consumer.json"))
    provider_auth = v2.AuthContextV2.model_validate(v2fix.load("auth_context_provider_dev.json"))
    dev = v2.DeploymentRevision.model_validate(DEV)
    serving = v2.ServingRevision.model_validate(SERVING)
    policy = v2.DataAccessPolicyRef.model_validate(v2fix.load("data_access_policy.json"))
    dev_card = v2.RateCardSnapshot.model_validate(
        dict(CARD, deployment_revision_id=dev.deployment_revision_id,
             rate_card_version="rc_internal_preview"))
    kwargs = dict(requested_model="marlin-2b-dev", deployment=dev, serving=serving,
                  rate_card=dev_card, policy=policy)
    with pytest.raises(errors.NotFound):
        v2ports.pin_admission(auth=auth, **kwargs)
    pins, _card = v2ports.pin_admission(auth=provider_auth, **kwargs)
    assert pins.deployment_revision_id == IDS.dev_deployment
    # the same provider audience pointed at another endpoint is refused
    with pytest.raises(errors.NotFound):
        v2ports.pin_admission(
            auth=provider_auth.model_copy(update={"endpoint_id": IDS.prod_endpoint}), **kwargs)
    # ... and a member of another provider cannot reach it either
    with pytest.raises(errors.NotFound):
        v2ports.pin_admission(
            auth=provider_auth.model_copy(update={"provider_org_id": IDS.rival_provider_org}),
            **kwargs)


def test_a_dev_deployment_can_never_be_public():
    with pytest.raises(ValidationError):
        v2.DeploymentRevision.model_validate(dict(DEV, visibility="public"))
    for state in ("draft", "validating", "ready_private", "retired"):
        with pytest.raises(ValidationError):
            v2.DeploymentRevision.model_validate(dict(PROD, state=state))
    assert v2.DeploymentRevision.model_validate(dict(PROD, state="draining")).state is \
        v2.DeploymentState.draining


def test_settle_cannot_reach_a_directory_or_a_current_rate():
    """The CREDIT-RATE invariant as a signature: `settle` takes the admission, the
    usage and a time. There is no catalog, no clock and no rate parameter."""
    assert list(inspect.signature(v2.settle).parameters) == ["admission", "usage", "settled_at"]
    source = inspect.getsource(v2.settle)
    for forbidden in ("active_rate_card", "catalog", "resolve("):
        assert forbidden not in source, f"settle reads {forbidden}"
    admission = v2.AdmissionV2.model_validate(ADMISSION)
    settlement = v2.settle(admission, v1.Usage.of(23500, 480), admission.admitted_at)
    assert settlement.rate_card_version == admission.pins.rate_card_version
    assert str(settlement.charged) == "9.97600000"
    assert settlement.charged <= admission.maximum_hold


def test_usage_beyond_the_envelope_and_unknown_usage_are_both_refused():
    admission = v2.AdmissionV2.model_validate(ADMISSION)
    with pytest.raises(ValueError, match="platform incident"):
        v2.settle(admission, v1.Usage.of(23500, 2048), admission.admitted_at)
    with pytest.raises(ValueError, match="authoritative"):
        v2.settle(admission, v1.Usage.of(10, 10, v1.UsageCertainty.unknown),
                  admission.admitted_at)


def test_the_meter_and_the_two_roundings_are_named_on_the_card():
    card = v2.RateCardSnapshot.model_validate(CARD)
    assert (card.meter, card.hold_rounding, card.debit_rounding) == \
        ("tokens-v1", "ceiling_8", "half_up_8")
    assert card.unit == mu.CREDIT and card.status == "approved"
    for bad in ({"meter": "seconds-v1"}, {"hold_rounding": "half_up_8"},
                {"debit_rounding": "ceiling_8"}, {"unit": "USD"}):
        with pytest.raises(ValidationError):
            v2.RateCardSnapshot.model_validate(dict(CARD, **bad))


def test_the_policy_version_pinned_and_attached_must_agree():
    body = v2fix.load("normalized_request.json")
    with pytest.raises(ValidationError):
        v2.NormalizedRequestV2.model_validate(
            dict(body, policy=dict(body["policy"], policy_version="dap_other")))
