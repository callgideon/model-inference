"""The exported contracts-v2 conformance cases.

One case per invariant the v2 oracles name (`research/plan/04-verification.md`):
`split_contract__`, `credit_units__`, `credit_identity__`, `credit_rate__`,
`credit_grant__`, `lab_access__`. A track runs the whole set against its own
directories:

    from infrx.contracts.conformance.v2_contracts import run_v2_conformance
    run_v2_conformance(my_harness_factory)      # raises AssertionError on failure

`factory() -> V2Harness` must return a **fresh** set of directories each call, so a
case that publishes a rate card or revokes a grant cannot leak into the next one.
Green against the fakes means implemented; only green against D1R's psycopg
directories means integrated.

Every case here is killable: `tests/contracts/mutants.py` (the one list) declares a
single-edit defect in `contracts/v2/*.py` for each invariant claimed below, and
the runner requires the *named* case to fail.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Callable

import pydantic

from .. import errors, money, records as v1
from ..v2 import fixtures as v2fix, money_units as mu, ports as v2ports, records as v2
from .v2_fakes import LATER, V2Harness, fake_v2_harness

IDS = v2fix.IDS


def _consumer_auth() -> v2.AuthContextV2:
    return v2fix.BUILDERS["auth_context_consumer.json"]()


def _provider_auth() -> v2.AuthContextV2:
    return v2fix.BUILDERS["auth_context_provider_dev.json"]()


async def _pin(harness: V2Harness, auth: v2.AuthContextV2, requested_model: str):
    """What an admission does: resolve through the directories, then freeze."""
    deployment = await harness.catalog.resolve(requested_model, audience=auth.audience,
                                               endpoint_id=auth.endpoint_id)
    serving = (await harness.catalog.serving_revision(deployment.serving_version_id)
               if deployment is not None else None)
    card = (await harness.catalog.active_rate_card(deployment.deployment_revision_id)
            if deployment is not None else None)
    policy = (await harness.catalog.data_access_policy(deployment.deployment_revision_id)
              if deployment is not None else None)
    return v2ports.pin_admission(auth=auth, requested_model=requested_model,
                                 deployment=deployment, serving=serving, rate_card=card,
                                 policy=policy)


async def _admit(harness: V2Harness, auth: v2.AuthContextV2 | None = None,
                 requested_model: str = v2fix.REQUESTED_MODEL) -> v2.AdmissionV2:
    auth = auth or _consumer_auth()
    wallet = v2ports.resolve_wallet(
        auth, await harness.wallets.consumer_wallet_for_user(auth.user_id))
    pins, card = await _pin(harness, auth, requested_model)
    return v2.AdmissionV2(
        request_id=IDS.request, job_handle="job_conformance0admission000000000001",
        org_id=auth.org_id, wallet_id=wallet.wallet_id, pins=pins, rate_card=card,
        maximum_hold=card.maximum_hold(23500, 512), admitted_at=harness.now)


# --- SPLIT-CONTRACT ----------------------------------------------------------
async def split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint(factory):
    """A public consumer key resolving a private dev deployment gets `not_found` —
    not a 403 that confirms the artifact exists, and never a successful pin."""
    harness = factory()
    dev_model = v2fix.DEV_REQUESTED_MODEL
    consumer = _consumer_auth()
    # Layer 1: the catalog does not list a private dev deployment for a consumer.
    assert await harness.catalog.resolve(dev_model, audience=consumer.audience,
                                        endpoint_id=consumer.endpoint_id) is None
    try:
        await _pin(harness, consumer, dev_model)
    except errors.NotFound as refused:
        assert "no published deployment" in str(refused), refused
    else:
        raise AssertionError("a consumer credential reached a private dev deployment")
    # Layer 2: and even handed the row directly, the pin refuses it. Each layer is
    # checked on its own, so removing either one fails this case.
    deployment = await harness.catalog.resolve(dev_model,
                                              audience=v2.CredentialAudience.provider_dev,
                                              endpoint_id=IDS.dev_endpoint)
    assert deployment is not None and deployment.endpoint_id == IDS.dev_endpoint
    serving = await harness.catalog.serving_revision(deployment.serving_version_id)
    policy = await harness.catalog.data_access_policy(deployment.deployment_revision_id)
    card = v2fix.BUILDERS["rate_card_marlin.json"]().model_copy(update={
        "deployment_revision_id": deployment.deployment_revision_id,
        "rate_card_version": "rc_internal_preview"})
    direct = dict(requested_model=dev_model, deployment=deployment, serving=serving,
                  rate_card=card, policy=policy)
    provider = _provider_auth()
    for wrong in (consumer,
                  provider.model_copy(update={"endpoint_id": IDS.prod_endpoint}),
                  provider.model_copy(update={"provider_org_id": IDS.rival_provider_org})):
        try:
            v2ports.pin_admission(auth=wrong, **direct)
        except errors.NotFound:
            continue
        raise AssertionError(f"{wrong.audience} reached the private dev deployment directly")
    # G1 (F2P review B1): `model_copy(update=)` skips AuthContextV2's validator, so a
    # consumer context can carry the dev endpoint's own ids and pass both equality
    # checks. Only the explicit audience check refuses it.
    forged = consumer.model_copy(update={"endpoint_id": deployment.endpoint_id,
                                         "provider_org_id": deployment.provider_org_id})
    try:
        v2ports.pin_admission(auth=forged, **direct)
    except errors.NotFound:
        pass
    else:
        raise AssertionError("a consumer context copied with the dev endpoint's scope "
                             "reached the private deployment")
    pins, _card = v2ports.pin_admission(auth=provider, **direct)
    assert pins.deployment_revision_id == deployment.deployment_revision_id


async def split_contract__a_provider_credential_cannot_borrow_another_endpoint(factory):
    """Endpoint scope is part of the credential: the same provider audience pointed
    at an endpoint it was not issued for resolves nothing."""
    harness = factory()
    auth = _provider_auth().model_copy(update={"endpoint_id": IDS.prod_endpoint})
    assert await harness.catalog.resolve(v2fix.DEV_REQUESTED_MODEL,
                                         audience=auth.audience,
                                         endpoint_id=auth.endpoint_id) is None


async def split_contract__an_internal_v1_payload_is_refused_not_upgraded(factory):
    """`schema_version` is explicit: a v1 body handed to a v2 reader is an error,
    never a silent reinterpretation, and the only projection is the named one."""
    factory()
    payload = dict(v2fix.load("wallet_consumer.json"), schema_version=1)
    try:
        v2.WalletRef.model_validate(payload)
    except pydantic.ValidationError as refused:
        assert "schema_version" in str(refused), refused
    else:
        raise AssertionError("a v1 payload was accepted as a v2 record")
    try:
        v2.upgrade_v1_price_snapshot_is_refused(
            v1.PriceSnapshot.model_validate(_v1_price_snapshot()))
    except ValueError as refused:
        assert "no conversion to CREDIT" in str(refused), refused
    else:
        raise AssertionError("a v1 USD price snapshot was converted to CREDIT")


def _v1_price_snapshot() -> dict:
    from .. import fixtures as v1fix
    return v1fix.load("price_snapshot.json")


async def split_contract__the_v1_model_revision_string_is_unchanged_r62(factory):
    """r1 R62: the consumer-facing identifier keeps its v1 form
    `<public_model_id>@<revision>` — byte-identical to what the v1 fixtures carry —
    and the artifact is pinned by the SERVING revision, not by that string. Reading
    an artifact identity out of `model_revision` is the confusion this case blocks."""
    harness = factory()
    serving = await harness.catalog.serving_revision(IDS.serving_version)
    assert serving.model_revision == v2fix.REQUESTED_MODEL == _v1_price_snapshot()[
        "model_revision"]
    assert "@" in serving.model_revision and serving.model_revision.count("@") == 1
    request = v2.NormalizedRequestV2.model_validate(v2fix.load("normalized_request.json"))
    assert request.request.model_revision == serving.model_revision
    assert request.pins.requested_model == serving.model_revision
    # the artifact identity lives here, and nowhere in the consumer string
    assert serving.model_commit == "fd111fca4fc7897876fb0d7e9df22ca5ac8ab965"
    assert len(serving.weight_shard_digests) == 2
    # ... and the provenance must be stated by whoever writes the row: a default here
    # would let a record claim `registry_oid_confirmed` without anyone confirming it.
    assert v2.ServingRevision.model_fields["digest_source"].is_required(), \
        "digest_source has a default; provenance must be stated, never assumed"
    assert serving.digest_source is v2.DigestSource.served_bytes, (
        "the registry-oid equality is still pending (W3); the record must say which "
        "kind of digest it holds rather than implying an upstream confirmation")
    assert serving.image_is_pinned is False, (
        "serve.sh pins the moving tag vllm/vllm-openai:nightly, so no image digest "
        "exists yet; W3 pulls by digest and fills it")
    assert serving.runtime_image_ref == "vllm/vllm-openai:nightly"


async def split_contract__the_surface_carries_one_reviewed_version(factory):
    """F2P item 7: the whole changed surface has ONE identifier, and the fixture
    base states it, so two halves of the tree cannot claim different revisions."""
    factory()
    from ..v2 import SURFACE_VERSION
    assert v2fix.load("map.json")["surface_version"] == SURFACE_VERSION
    assert v2fix.load("map.json")["schema_version"] == v2.SCHEMA_VERSION == 2


# --- CREDIT-UNITS ------------------------------------------------------------
async def credit_units__mixed_unit_arithmetic_is_refused_by_construction(factory):
    """Adding, subtracting or comparing two denominations is a `TypeError` from the
    interpreter, and no function anywhere converts one into the other."""
    factory()
    credit, usd, provider = mu.Credit("1.00000000"), mu.Usd("1.00000000"), mu.ProviderUsd("1")
    for left, right in ((credit, usd), (usd, provider), (provider, credit)):
        for operation in (lambda a, b: a + b, lambda a, b: a - b, lambda a, b: a < b):
            try:
                operation(left, right)
            except TypeError:
                continue
            raise AssertionError(f"{type(left).__name__} combined with {type(right).__name__}")
        assert (left == right) is False, "two units compared equal"
    # and neither can be cast into the other
    for target, source in ((mu.Credit, usd), (mu.Usd, credit), (mu.ProviderUsd, credit)):
        try:
            target(source)
        except ValueError:
            continue
        raise AssertionError(f"{source!r} was cast into {target.__name__}")
    assert credit.raw(mu.CREDIT) == money.parse("1")
    try:
        credit.raw(mu.USD)
    except TypeError:
        pass
    else:
        raise AssertionError("a Credit answered a USD unwrap")


async def credit_units__a_mixed_history_totals_per_unit_and_never_once(factory):
    """A history holding both regimes reports one figure per unit. There is no
    combined total, because no rate exists at which the two could be combined."""
    factory()
    history = v2.UsageHistory.model_validate(v2fix.load("usage_history_mixed.json"))
    totals = history.totals()
    assert set(totals) == {"CREDIT", "USD"}, totals
    assert totals["USD"] == "0.01414000" and totals["CREDIT"] == "9.97600000", totals
    # the legacy row's value is exactly what it always was
    legacy = [e for e in history.entries if e.accounting_regime is v2.AccountingRegime.legacy_usd]
    assert len(legacy) == 1 and str(legacy[0].amount()) == "0.01414000"
    assert isinstance(legacy[0].amount(), mu.Usd)
    # and the regime, not the `unit` column, is what fixes the denomination: a legacy
    # row relabelled CREDIT is refused rather than read as credits.
    try:
        v2.UsageRecordV2.model_validate(dict(v2fix.load("usage_legacy_usd.json"),
                                             unit=mu.CREDIT))
    except pydantic.ValidationError as refused:
        assert "denominated in" in str(refused), refused
    else:
        raise AssertionError("a legacy_usd row was accepted as CREDIT")


async def credit_units__a_legacy_row_invents_none_of_the_new_fields(factory):
    """The v1 read projection: an old row has no rate card and no serving revision,
    and they read back absent rather than as a plausible default."""
    factory()
    row = v2.UsageRecordV2.model_validate(v2fix.load("usage_legacy_usd.json"))
    assert row.accounting_regime is v2.AccountingRegime.legacy_usd and row.unit == mu.USD
    assert row.rate_card_version is None and row.serving_version_id is None
    assert row.deployment_revision_id is None and row.price_version == "pv_2026_09_01"
    # a row that never recorded a price version keeps None, not a plausible default
    bare = v2.project_v1_usage(
        {"request_id": IDS.legacy_request, "cost_usd": "0.00000000", "prompt_tokens": 0,
         "completion_tokens": 0, "settled_at": v2fix.T0}, org_id=IDS.consumer_org)
    assert bare.price_version is None, bare.price_version
    # and a CREDIT row may not borrow the legacy field, or vice versa
    try:
        v2.UsageRecordV2.model_validate(dict(v2fix.load("usage_credit.json"),
                                             price_version="pv_2026_09_01"))
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError("a CREDIT row carried a legacy price_version")


async def credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist(factory):
    """The unit and the meter are literals on the record, so a USD card, an unknown
    meter or a negative rate is refused at construction, before admission sees it."""
    factory()
    card = v2fix.load("rate_card_marlin.json")
    for bad in ({"unit": "USD"}, {"meter": "seconds-v1"},
                {"input_rate_per_million": "-1.00000000"}, {"status": "proposed"},
                {"approved_by": "   "}):
        try:
            v2.RateCardSnapshot.model_validate(dict(card, **bad))
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"a rate card was accepted with {bad}")
    assert v2.RateCardSnapshot.model_validate(card).unit == mu.CREDIT


async def credit_units__a_nonzero_legacy_balance_is_a_hold_not_a_conversion(factory):
    """`02-credits.md` chooses no conversion rate, so a nonzero legacy USD balance
    is an explicit rollout hold for that account and never an implicit credit."""
    factory()
    statement = v2fix.load("legacy_usd_statement.json")
    assert v2.LegacyUsdStatement.model_validate(statement).rollout_hold is True
    try:
        v2.LegacyUsdStatement.model_validate(dict(statement, rollout_hold=False))
    except pydantic.ValidationError as refused:
        assert "rollout hold" in str(refused), refused
    else:
        raise AssertionError("a nonzero legacy balance passed without a hold")
    balance = v2.BalanceV2.model_validate(v2fix.load("balance.json"))
    assert balance.legacy_usd is not None and balance.unit == mu.CREDIT
    assert isinstance(balance.legacy_usd.balance, mu.Usd)
    assert isinstance(balance.ledger_total, mu.Credit)


# --- CREDIT-IDENTITY ---------------------------------------------------------
async def credit_identity__a_consumer_credential_resolves_its_own_user_wallet(factory):
    """From trusted data only: the wallet owned by the credential's `user_id`, bound
    to that user's personal consumer organization."""
    harness = factory()
    auth = _consumer_auth()
    wallet = v2ports.resolve_wallet(
        auth, await harness.wallets.consumer_wallet_for_user(auth.user_id))
    assert wallet.kind is v2.WalletKind.consumer
    assert wallet.owner_user_id == auth.user_id
    assert wallet.personal_org_id == auth.org_id
    assert wallet.available == mu.Credit("9989.98560000"), str(wallet.available)


async def credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet(factory):
    """A provider dev credential gets its provider's operator-funded wallet: zero
    balance, no signup entitlement, and never a consumer wallet."""
    harness = factory()
    auth = _provider_auth()
    wallet = v2ports.resolve_wallet(
        auth, await harness.wallets.provider_dev_wallet(auth.provider_org_id))
    assert wallet.kind is v2.WalletKind.provider_dev
    assert wallet.ledger_total.is_zero and wallet.reserved_total.is_zero
    assert wallet.has_signup_entitlement is False
    assert wallet.owner_user_id is None and wallet.personal_org_id is None
    # handed a consumer wallet, the same credential is refused...
    try:
        v2ports.resolve_wallet(auth,
                               await harness.wallets.consumer_wallet_for_user(IDS.consumer_user))
    except errors.Forbidden:
        pass
    else:
        raise AssertionError("a provider credential spent a consumer wallet")
    # ... and so is a *rival provider's* dev wallet, which passes the kind check and
    # fails only on ownership. Each guard is therefore checked on its own.
    try:
        v2ports.resolve_wallet(auth, wallet.model_copy(update={
            "wallet_id": IDS.rival_provider_org,
            "owner_provider_org_id": IDS.rival_provider_org}))
    except errors.Forbidden:
        pass
    else:
        raise AssertionError("a provider credential spent another provider's wallet")
    # G3 (F2P review B1): a consumer wallet copied with this provider's id as owner.
    # `model_copy(update=)` skips WalletRef's ownership validator, so ownership
    # matches and only the explicit kind check refuses it.
    consumer_wallet = await harness.wallets.consumer_wallet_for_user(IDS.consumer_user)
    try:
        v2ports.resolve_wallet(auth, consumer_wallet.model_copy(update={
            "owner_provider_org_id": auth.provider_org_id}))
    except errors.Forbidden:
        return
    raise AssertionError("a provider credential spent a consumer wallet whose owner fields "
                         "were copied to match")


async def credit_identity__no_request_field_can_select_a_wallet(factory):
    """There is nowhere for a caller-supplied wallet to enter: the auth context has
    no wallet field and refuses one, and the public request shape has none either."""
    factory()
    for payload_extra in ({"wallet_id": IDS.other_wallet}, {"wallet": IDS.other_wallet}):
        try:
            v2.AuthContextV2.model_validate(dict(v2fix.load("auth_context_consumer.json"),
                                                 **payload_extra))
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"an auth context accepted {payload_extra}")
    assert "wallet_id" not in v1.NormalizedRequest.model_fields
    assert "wallet_id" not in set(v2.AuthContextV2.model_fields)
    # the v2 request does carry one, and it is written by admission, not parsed
    # from a body: the public half is the nested v1 record, which has no such field
    assert "wallet_id" in v2.NormalizedRequestV2.model_fields


async def credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback(factory):
    """A wallet that is not this credential's own is `Forbidden` — the resolver
    never falls back to "some wallet" and never spends across users."""
    harness = factory()
    auth = _consumer_auth()
    mine = await harness.wallets.consumer_wallet_for_user(auth.user_id)
    foreign = mine.model_copy(update={"wallet_id": IDS.other_wallet,
                                      "owner_user_id": IDS.other_user})
    other_org = mine.model_copy(update={"personal_org_id": IDS.other_org})
    for wallet in (foreign, other_org):
        try:
            v2ports.resolve_wallet(auth, wallet)
        except errors.Forbidden:
            continue
        raise AssertionError("a credential spent a wallet it does not own")
    try:
        v2ports.resolve_wallet(auth, None)
    except errors.NotFound:
        pass
    else:
        raise AssertionError("a missing wallet resolved to something")
    # G2 (F2P review B1): a provider_dev wallet copied with this consumer's owner and
    # personal org. `model_copy(update=)` skips WalletRef's ownership validator, so
    # both equalities pass and only the explicit kind check refuses it.
    provider_wallet = await harness.wallets.provider_dev_wallet(IDS.provider_org)
    try:
        v2ports.resolve_wallet(auth, provider_wallet.model_copy(update={
            "owner_user_id": auth.user_id, "personal_org_id": auth.org_id}))
    except errors.Forbidden:
        return
    raise AssertionError("a consumer credential spent a provider_dev wallet whose owner "
                         "fields were copied to match")


async def credit_identity__an_operator_credential_spends_no_wallet(factory):
    """Operator actions are audited grants and adjustments, not inference on a
    customer's balance."""
    harness = factory()
    operator = v2.AuthContextV2(
        audience=v2.CredentialAudience.operator, org_id=IDS.consumer_org,
        key_id=IDS.consumer_key, principal="ops@platform", role=v1.Role.operator,
        entitlement_version=1)
    try:
        v2ports.resolve_wallet(
            operator, await harness.wallets.consumer_wallet_for_user(IDS.consumer_user))
    except errors.Forbidden:
        return
    raise AssertionError("an operator credential resolved a spendable wallet")


async def credit_identity__a_provider_wallet_has_no_grant_and_no_transfer(factory):
    """A provider dev wallet cannot receive the signup grant, and the closed ledger
    vocabulary contains no operation that would move its balance to a consumer."""
    harness = factory()
    provider_wallet = await harness.wallets.provider_dev_wallet(IDS.provider_org)
    try:
        v2.issue_signup_grant(wallet=provider_wallet, user_id=IDS.consumer_user,
                              verification_evidence_ref="evidence",
                              ledger_operation_id=IDS.signup_operation, granted_at=harness.now)
    except ValueError as refused:
        assert "no signup entitlement" in str(refused), refused
    else:
        raise AssertionError("a provider_dev wallet received a signup grant")
    kinds = {kind.value for kind in v2.LedgerEntryKind}
    assert not any("transfer" in kind for kind in kinds), kinds
    assert kinds == {"signup_grant", "operator_allocation", "operator_adjustment",
                     "inference_debit"}, kinds
    # a signup_grant entry against a provider wallet cannot even be recorded
    try:
        v2.CreditLedgerEntry.model_validate(
            dict(v2fix.load("credit_ledger_signup.json"), wallet_kind="provider_dev"))
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError("a provider wallet carried a signup grant entry")


async def credit_identity__campaign_and_membership_never_reset_the_grant_key(factory):
    """The uniqueness key is `(user_id, initial_signup_grant)`. Changing campaign
    metadata, joining a provider workspace or owning another organization leaves it
    identical, so the same unique index still refuses a second grant."""
    harness = factory()
    wallet = await harness.wallets.consumer_wallet_for_user(IDS.consumer_user)
    first, _ = v2.issue_signup_grant(
        wallet=wallet, user_id=IDS.consumer_user, verification_evidence_ref="evidence/1",
        ledger_operation_id=IDS.signup_operation, granted_at=harness.now,
        campaign_version="launch_2026_09")
    again, _ = v2.issue_signup_grant(
        wallet=wallet, user_id=IDS.consumer_user, verification_evidence_ref="evidence/2",
        ledger_operation_id=IDS.access_grant, granted_at=harness.now + timedelta(days=30),
        campaign_version="relaunch_2027_01")
    assert first.key == again.key == (IDS.consumer_user, v2.INITIAL_SIGNUP_ENTITLEMENT)
    assert first.campaign_version != again.campaign_version, "the fixture must vary the campaign"
    # a provider membership grants nothing here
    membership = await harness.providers.membership(IDS.provider_org, IDS.provider_member)
    assert membership is not None and membership.provider_org_id != wallet.personal_org_id


# --- CREDIT-GRANT ------------------------------------------------------------
async def credit_grant__the_grant_is_exactly_ten_thousand_credit_once(factory):
    """+10000.00000000 CREDIT, the wallet, the verification evidence and one ledger
    operation — as one operation, never two halves that can diverge."""
    harness = factory()
    wallet = await harness.wallets.consumer_wallet_for_user(IDS.consumer_user)
    grant, entry = v2.issue_signup_grant(
        wallet=wallet, user_id=IDS.consumer_user,
        verification_evidence_ref="email_verification/2026-09-22/a0000001",
        ledger_operation_id=IDS.signup_operation, granted_at=harness.now)
    assert grant.amount == mu.Credit("10000.00000000") == v2.INITIAL_SIGNUP_GRANT
    assert entry.amount == grant.amount and entry.kind is v2.LedgerEntryKind.signup_grant
    assert entry.operation_id == grant.ledger_operation_id == IDS.signup_operation
    assert entry.wallet_id == grant.wallet_id == wallet.wallet_id
    assert grant.verification_evidence_ref
    for bad in ({"amount": "9999.00000000"}, {"amount": "10001.00000000"},
                {"verification_evidence_ref": " "}):
        try:
            v2.SignupGrant.model_validate(dict(v2fix.load("signup_grant.json"), **bad))
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"a signup grant was accepted with {bad}")


async def credit_grant__the_grant_lands_only_in_the_individuals_own_wallet(factory):
    """An identity cannot point its grant at somebody else's wallet, whatever the
    membership or organization situation is."""
    harness = factory()
    wallet = await harness.wallets.consumer_wallet_for_user(IDS.consumer_user)
    try:
        v2.issue_signup_grant(wallet=wallet, user_id=IDS.other_user,
                              verification_evidence_ref="evidence",
                              ledger_operation_id=IDS.signup_operation, granted_at=harness.now)
    except ValueError as refused:
        assert "own wallet" in str(refused), refused
    else:
        raise AssertionError("a grant landed in another user's wallet")


# --- CREDIT-RATE -------------------------------------------------------------
async def credit_rate__admission_pins_model_serving_deployment_and_rate_card(factory):
    """Acceptance freezes `model -> deployment revision -> serving version + rate
    card version + policy version`, and the attached card must be the pinned one."""
    harness = factory()
    admission = await _admit(harness)
    pins = admission.pins
    assert pins.model_id == IDS.model
    assert pins.deployment_revision_id == IDS.prod_deployment
    assert pins.serving_version_id == IDS.serving_version
    assert pins.rate_card_version == v2fix.RATE_CARD_VERSION
    assert pins.policy_version == v2fix.POLICY_VERSION
    assert pins.accounting_regime == "credit"
    # a card that prices something else cannot be attached to those pins
    body = admission.model_dump(mode="json", exclude_none=True)
    for swap in ({"rate_card_version": "rc_other"},
                 {"deployment_revision_id": IDS.dev_deployment},
                 {"serving_version_id": IDS.model_version}):
        try:
            v2.AdmissionV2.model_validate(dict(body, rate_card=dict(body["rate_card"], **swap)))
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"an admission carried a card it did not pin: {swap}")


async def credit_rate__a_rate_published_after_acceptance_does_not_move_the_job(factory):
    """CREDIT-RATE: an operator doubling the published rate while the job waits
    changes future admissions only. This one settles at the card it was admitted
    with, and so does its idempotent replay."""
    harness = factory()
    admission = await _admit(harness)
    harness.catalog.publish(admission.rate_card.model_copy(update={
        "rate_card_version": "rc_marlin2b_2026_10", "input_rate_per_million": mu.Credit("800"),
        "output_rate_per_million": mu.Credit("2400")}))
    usage = v1.Usage.of(23500, 480)
    settlement = v2.settle(admission, usage, harness.now + timedelta(seconds=30))
    assert settlement.rate_card_version == v2fix.RATE_CARD_VERSION
    assert settlement.charged == mu.Credit("9.97600000"), str(settlement.charged)
    assert settlement.serving_version_id == IDS.serving_version
    replay = admission.model_copy(update={"replayed": True})
    assert v2.settle(replay, usage, harness.now + timedelta(seconds=31)).charged == \
        settlement.charged
    # a freshly admitted request does see the new rate - and pins the card it resolved,
    # checked on the pins before an AdmissionV2 is built from them (whose validator would
    # otherwise be what notices a pin that disagrees with its card)
    pins, card = await _pin(harness, _consumer_auth(), v2fix.REQUESTED_MODEL)
    assert pins.rate_card_version == card.rate_card_version == "rc_marlin2b_2026_10"
    fresh = await _admit(harness)
    assert fresh.pins.rate_card_version == "rc_marlin2b_2026_10"
    assert fresh.maximum_hold > admission.maximum_hold


async def credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job(factory):
    """Promotion and rollback change future resolution. An admitted job keeps its
    serving and deployment revision; in-flight work never changes revision."""
    harness = factory()
    admission = await _admit(harness)
    harness.catalog.move_alias(v2fix.REQUESTED_MODEL, IDS.dev_deployment)
    settlement = v2.settle(admission, v1.Usage.of(10, 10), harness.now + timedelta(seconds=5))
    assert settlement.serving_version_id == admission.pins.serving_version_id
    assert settlement.deployment_revision_id == IDS.prod_deployment


async def credit_rate__an_unknown_private_or_unpriced_model_is_refused(factory):
    """Three refusals, none of them free inference: an unknown model and a private
    dev deployment are `not_found`; a deployment with no approved active CREDIT card
    is `invalid_request`, because unpriced is unserveable, not free."""
    harness = factory()
    consumer = _consumer_auth()
    for model in ("no/such-model", v2fix.DEV_REQUESTED_MODEL):
        try:
            await _pin(harness, consumer, model)
        except errors.NotFound:
            continue
        raise AssertionError(f"{model} resolved for a consumer credential")
    provider = _provider_auth()
    try:
        await _pin(harness, provider, v2fix.DEV_REQUESTED_MODEL)
    except errors.InvalidRequest as refused:
        assert "no approved CREDIT rate card" in str(refused), refused
    else:
        raise AssertionError("an unpriced dev deployment was admitted")


async def credit_rate__a_caller_supplied_price_or_identity_is_refused(factory):
    """R45 extended to v2: the request body has no price, rate card, serving
    revision or organization field, so a caller cannot supply one."""
    harness = factory()
    body = v2fix.load("normalized_request.json")["request"]
    for forged in ({"input_rate_per_million": "0.00000001"},
                   {"rate_card_version": "rc_free"},
                   {"serving_version_id": IDS.serving_version},
                   {"price_snapshot": {"currency": "USD"}}):
        try:
            v1.NormalizedRequest.model_validate(dict(body, **forged))
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"the public request shape accepted {forged}")
    # and pin_admission has no parameter a caller could route one through
    signature = v2ports.pin_admission.__code__.co_varnames[
        :v2ports.pin_admission.__code__.co_argcount
        + v2ports.pin_admission.__code__.co_kwonlyargcount]
    assert set(signature) == {"auth", "requested_model", "deployment", "serving", "rate_card",
                              "policy"}, signature
    pins, card = await _pin(harness, _consumer_auth(), v2fix.REQUESTED_MODEL)
    assert card.rate_card_version == pins.rate_card_version


async def credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once(factory):
    """`02-credits.md`: the maximum hold rounds away from the customer's favour and
    the final charge rounds half up exactly once, both at eight places."""
    harness = factory()
    admission = await _admit(harness)
    card = admission.rate_card
    # A cost finer than 1e-8 is where the two roundings differ: at 0.001 CREDIT per
    # million, one token costs 1e-9. The hold must round it UP to 0.00000001 and the
    # charge must round it half-up DOWN to zero. Equal roundings would make the
    # platform reserve less than it may charge.
    odd = card.model_copy(update={"input_rate_per_million": mu.Credit("0.00100000"),
                                  "output_rate_per_million": mu.Credit("0.00100000")})
    assert str(odd.maximum_hold(1, 0)) == "0.00000001", str(odd.maximum_hold(1, 0))
    assert str(odd.debit(1, 0)) == "0.00000000", str(odd.debit(1, 0))
    assert str(odd.debit(5, 0)) == "0.00000001", str(odd.debit(5, 0))   # 5e-9 -> half up
    assert str(odd.debit(4, 0)) == "0.00000000", str(odd.debit(4, 0))
    assert str(card.maximum_hold(23500, 512)) == "10.01440000"
    assert str(card.debit(23500, 480)) == "9.97600000"
    assert admission.maximum_hold >= v2.settle(
        admission, v1.Usage.of(23500, 480), harness.now).charged
    # usage beyond the reserved envelope is a platform incident, not a debit
    try:
        v2.settle(admission, v1.Usage.of(23500, 2048), harness.now)
    except ValueError as refused:
        assert "platform incident" in str(refused), refused
    else:
        raise AssertionError("a settlement exceeded the admitted hold")


async def credit_rate__admitted_pins_are_immutable_and_every_pin_is_required(factory):
    """An admitted job's pins cannot be edited in place and none of them may be left
    to a default. Frozen records refuse attribute assignment; every identity field of
    `AdmissionPins` and `AdmissionV2` is required. (`model_copy(update=)` still
    produces a *new* object; proposed ruling V15 forbids it on admitted pins, and
    wire-in/D2–D5 enforce that at the persistence boundary.)"""
    harness = factory()
    admission = await _admit(harness)
    for target, field, value in ((admission.pins, "serving_version_id", IDS.model_version),
                                 (admission.pins, "rate_card_version", "rc_other"),
                                 (admission, "rate_card", admission.rate_card),
                                 (admission, "maximum_hold", mu.Credit("0"))):
        try:
            setattr(target, field, value)
        except pydantic.ValidationError:
            continue
        raise AssertionError(f"{type(target).__name__}.{field} was assigned in place")
    # Only single-valued constants and the replay flag may default.
    required_pins = {name for name, f in v2.AdmissionPins.model_fields.items()
                     if f.is_required()}
    assert required_pins == {"model_id", "requested_model", "deployment_revision_id",
                             "serving_version_id", "rate_card_version",
                             "policy_version"}, sorted(required_pins)
    required_admission = {name for name, f in v2.AdmissionV2.model_fields.items()
                          if f.is_required()}
    assert required_admission == {"request_id", "job_handle", "org_id", "wallet_id", "pins",
                                  "rate_card", "maximum_hold", "admitted_at"}, \
        sorted(required_admission)


async def credit_rate__unknown_usage_is_never_settled(factory):
    """Only authoritative engine usage settles a debit; unknown usage is quarantined
    and reconciled, never debited later."""
    harness = factory()
    admission = await _admit(harness)
    unknown = v1.Usage.of(23500, 480, v1.UsageCertainty.unknown)
    try:
        v2.settle(admission, unknown, harness.now)
    except ValueError as refused:
        assert "authoritative" in str(refused), refused
    else:
        raise AssertionError("unknown usage produced a debit")


# --- LAB-ACCESS --------------------------------------------------------------
async def lab_access__provider_ownership_alone_yields_no_customer_payload(factory):
    """The provider owns the model and the deployment, and that is not permission to
    read a customer's content: no role's capability set contains it."""
    harness = factory()
    for role, capabilities in v2.ROLE_CAPABILITIES.items():
        assert v2.ProviderCapability.read_customer_content not in capabilities, role
    membership = await harness.providers.membership(IDS.provider_org, IDS.provider_member)
    assert membership.permits(v2.ProviderCapability.read_customer_content, harness.now,
                              IDS.provider_org) is False
    # with a current grant for that purpose, the same request is allowed
    grant = await harness.providers.current_grant(IDS.consumer_org, IDS.provider_org)
    assert v2.may_read_customer_content(
        membership=membership, grant=grant, now=harness.now, provider_org_id=IDS.provider_org,
        model_id=IDS.model, category=v2.DataCategory.request_content,
        purpose=v2.DataPurpose.provider_sharing) is True
    # and with no grant at all it is denied, not defaulted
    assert v2.may_read_customer_content(
        membership=membership, grant=None, now=harness.now, provider_org_id=IDS.provider_org,
        model_id=IDS.model, category=v2.DataCategory.request_content,
        purpose=v2.DataPurpose.provider_sharing) is False


async def lab_access__a_revoked_grant_blocks_access_immediately(factory):
    """Revocation is checked against the *current* grant, not a snapshot taken when
    the data was captured: the same membership and the same row stop being readable."""
    harness = factory()
    membership = await harness.providers.membership(IDS.provider_org, IDS.provider_member)
    before = await harness.providers.current_grant(IDS.consumer_org, IDS.provider_org)
    assert before.is_current(harness.now)
    harness.providers.revoke(IDS.consumer_org, IDS.provider_org, harness.now)
    after = await harness.providers.current_grant(IDS.consumer_org, IDS.provider_org)
    assert after.is_current(harness.now) is False
    assert v2.may_read_customer_content(
        membership=membership, grant=after, now=harness.now, provider_org_id=IDS.provider_org,
        model_id=IDS.model, category=v2.DataCategory.request_content,
        purpose=v2.DataPurpose.provider_sharing) is False
    # the pre-revocation snapshot is audit evidence, not standing permission
    assert before.version != after.version
    try:
        v2ports.authorize_content_read(
            membership=membership, grant=after, now=harness.now,
            provider_org_id=IDS.provider_org, model_id=IDS.model,
            category=v2.DataCategory.request_content, purpose=v2.DataPurpose.provider_sharing)
    except errors.Forbidden:
        return
    raise AssertionError("a revoked grant still authorized a content read")


async def lab_access__an_expired_grant_and_a_rival_provider_are_refused(factory):
    """Two providers, one consumer: expiry and recipient are both checked, so a
    rival provider and a lapsed window are refused with no role escalation."""
    harness = factory()
    membership = await harness.providers.membership(IDS.provider_org, IDS.provider_member)
    grant = await harness.providers.current_grant(IDS.consumer_org, IDS.provider_org)
    assert grant.is_current(LATER) is False, "the fixture grant must expire before LATER"
    assert v2.may_read_customer_content(
        membership=membership, grant=grant, now=LATER, provider_org_id=IDS.provider_org,
        model_id=IDS.model, category=v2.DataCategory.request_content,
        purpose=v2.DataPurpose.provider_sharing) is False
    assert grant.permits(now=harness.now, provider_org_id=IDS.rival_provider_org,
                         model_id=IDS.model, category=v2.DataCategory.request_content,
                         purpose=v2.DataPurpose.provider_sharing) is False
    assert await harness.providers.membership(IDS.rival_provider_org,
                                             IDS.provider_member) is None
    assert grant.permits(now=harness.now, provider_org_id=IDS.provider_org,
                         model_id="d0000009-0000-4000-8000-000000000009",
                         category=v2.DataCategory.request_content,
                         purpose=v2.DataPurpose.provider_sharing) is False


async def lab_access__each_purpose_is_a_separate_permission(factory):
    """Capture, provider sharing, external judging and training are four
    permissions. A grant for one says nothing about the other three."""
    harness = factory()
    grant = await harness.providers.current_grant(IDS.consumer_org, IDS.provider_org)
    assert grant.purposes == (v2.DataPurpose.provider_sharing,)
    for purpose in (v2.DataPurpose.capture, v2.DataPurpose.external_judging,
                    v2.DataPurpose.training):
        assert grant.permits(now=harness.now, provider_org_id=IDS.provider_org,
                             model_id=IDS.model,
                             category=v2.DataCategory.request_content,
                             purpose=purpose) is False, purpose
    assert grant.permits(now=harness.now, provider_org_id=IDS.provider_org, model_id=IDS.model,
                         category=v2.DataCategory.media,
                         purpose=v2.DataPurpose.provider_sharing) is False
    assert {p.value for p in v2.DataPurpose} == {"capture", "provider_sharing",
                                                "external_judging", "training"}


async def lab_access__roles_default_deny_and_a_viewer_reaches_nothing(factory):
    """New permissions default deny: a viewer has aggregate health and nothing else,
    a revoked membership has nothing, and a member of another provider has nothing."""
    harness = factory()
    membership = await harness.providers.membership(IDS.provider_org, IDS.provider_member)
    viewer = membership.model_copy(update={"role": v2.ProviderRole.viewer})
    assert viewer.permits(v2.ProviderCapability.read_aggregate_health, harness.now,
                          IDS.provider_org) is True
    for capability in (v2.ProviderCapability.manage_dev_deployment,
                       v2.ProviderCapability.propose_publication,
                       v2.ProviderCapability.manage_members,
                       v2.ProviderCapability.read_customer_content):
        assert viewer.permits(capability, harness.now, IDS.provider_org) is False, capability
    revoked = membership.model_copy(update={"revoked_at": harness.now})
    assert revoked.permits(v2.ProviderCapability.read_aggregate_health, harness.now,
                           IDS.provider_org) is False
    assert membership.permits(v2.ProviderCapability.manage_dev_deployment, harness.now,
                              IDS.rival_provider_org) is False
    assert membership.permits(v2.ProviderCapability.propose_publication, harness.now,
                              IDS.provider_org) is False


def cases() -> list[Callable]:
    """Every exported v2 case, in oracle order. A track runs all of them."""
    return [
        split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint,
        split_contract__a_provider_credential_cannot_borrow_another_endpoint,
        split_contract__an_internal_v1_payload_is_refused_not_upgraded,
        split_contract__the_v1_model_revision_string_is_unchanged_r62,
        split_contract__the_surface_carries_one_reviewed_version,
        credit_units__mixed_unit_arithmetic_is_refused_by_construction,
        credit_units__a_mixed_history_totals_per_unit_and_never_once,
        credit_units__a_legacy_row_invents_none_of_the_new_fields,
        credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist,
        credit_units__a_nonzero_legacy_balance_is_a_hold_not_a_conversion,
        credit_identity__a_consumer_credential_resolves_its_own_user_wallet,
        credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet,
        credit_identity__no_request_field_can_select_a_wallet,
        credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback,
        credit_identity__an_operator_credential_spends_no_wallet,
        credit_identity__a_provider_wallet_has_no_grant_and_no_transfer,
        credit_identity__campaign_and_membership_never_reset_the_grant_key,
        credit_grant__the_grant_is_exactly_ten_thousand_credit_once,
        credit_grant__the_grant_lands_only_in_the_individuals_own_wallet,
        credit_rate__admission_pins_model_serving_deployment_and_rate_card,
        credit_rate__a_rate_published_after_acceptance_does_not_move_the_job,
        credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job,
        credit_rate__an_unknown_private_or_unpriced_model_is_refused,
        credit_rate__a_caller_supplied_price_or_identity_is_refused,
        credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once,
        credit_rate__unknown_usage_is_never_settled,
        credit_rate__admitted_pins_are_immutable_and_every_pin_is_required,
        lab_access__provider_ownership_alone_yields_no_customer_payload,
        lab_access__a_revoked_grant_blocks_access_immediately,
        lab_access__an_expired_grant_and_a_rival_provider_are_refused,
        lab_access__each_purpose_is_a_separate_permission,
        lab_access__roles_default_deny_and_a_viewer_reaches_nothing,
    ]


# --- the CREDIT regime of the JobStore (F2P wire-in, items 3-4) -------------------
# `ports.CreditJobStore`, driven through a port `Harness` like v1's JobStore suite. D2/D5
# run these against PostgreSQL. Required hooks (read directly, like v1's `grant`/`balance`):
# `credit_balance(wallet_id) -> {"ledger", "reserved", "available"}` (Decimal),
# `credit_grant(wallet_id, amount)` (an audited allocation), `register_credential(auth)`
# (a key row), `publish_rate_card(card)` (an operator publishing a card), and v1's
# `balance(org_id)` for the organization's USD wallet.
def _credit_request(harness, *, provider: bool = False, model: str | None = None, **kw):
    from . import builders as b
    if provider:
        return b.request(harness, org_id=IDS.provider_org, key_id=IDS.provider_dev_key,
                         model_revision=model or v2fix.DEV_REQUESTED_MODEL, **kw)
    return b.request(harness, org_id=IDS.consumer_org, key_id=IDS.consumer_key,
                     model_revision=model or v2fix.REQUESTED_MODEL, **kw)


def _card(**changes) -> v2.RateCardSnapshot:
    """The fixture card with named changes, revalidated (R78: never `model_copy(update=)`)."""
    base = v2fix.BUILDERS["rate_card_marlin.json"]().model_dump(mode="json")
    return v2.RateCardSnapshot.model_validate({**base, **changes})


async def _refused(call, error):
    try:
        await call
    except error:
        return
    raise AssertionError(f"expected {error.__name__}")


async def _credit_run(harness, request, admission):
    """Prepare and claim a CREDIT job: a live inference lease."""
    port = harness.port
    lease = await port.claim_preparation(request.request_id, "prep-a")
    prepared = await port.prepared(lease)
    assert prepared == admission, "prepared answered a CREDIT job with something else"
    return await port.claim(request.request_id, "worker-a")


async def credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit(factory):
    """CREDIT-IDENTITY/CREDIT-RATE/CREDIT-UNITS at admission: the wallet is the
    credential's own (R66), the pins and card are the catalog's (R69), the hold is the
    card's ceiling-rounded maximum on that CREDIT wallet, and the organization's USD
    wallet is untouched. A v1 read of the job is `not_found`."""
    harness = factory()
    request = _credit_request(harness)
    before = harness.extra["credit_balance"](IDS.consumer_wallet)
    admission = await harness.port.admit_credit(request, b_idem(request))
    card = v2fix.BUILDERS["rate_card_marlin.json"]()
    assert isinstance(admission, v2.AdmissionV2) and admission.replayed is False
    assert admission.wallet_id == IDS.consumer_wallet
    assert admission.pins == v2fix.BUILDERS["admission_pins.json"]()
    assert admission.rate_card == card
    hold = card.maximum_hold(request.max_input_tokens, request.max_output_tokens)
    assert admission.maximum_hold == hold
    after = harness.extra["credit_balance"](IDS.consumer_wallet)
    assert after["reserved"] == before["reserved"] + hold.raw(mu.CREDIT)
    assert after["ledger"] == before["ledger"]
    assert harness.extra["balance"](IDS.consumer_org)["reserved"] == 0, \
        "a CREDIT hold landed on the organization's USD wallet"
    owned, outcome = await harness.port.get_owned_credit(IDS.consumer_org, admission.job_handle)
    assert owned == admission and outcome is None
    await _refused(harness.port.get_owned(IDS.consumer_org, admission.job_handle),
                   errors.NotFound)


async def credit_admit__refusals_leave_no_job_and_no_hold(factory):
    """CREDIT-IDENTITY/R69/R70 in the transaction: a consumer key cannot reach a private
    dev model and an unknown model is `not_found`; an unpriced model is
    `invalid_request`; an operator key spends no wallet (`forbidden`); an unfunded
    provider dev wallet is `insufficient_credit` until an operator allocates to it. No
    refusal leaves a job or a hold behind."""
    from . import builders as b
    harness = factory()
    port = harness.port
    reserved = harness.extra["credit_balance"](IDS.consumer_wallet)["reserved"]
    for request, error in (
            (_credit_request(harness, model=v2fix.DEV_REQUESTED_MODEL), errors.NotFound),
            (_credit_request(harness, model="nemostation/unknown@2026-09-01"), errors.NotFound),
            (_credit_request(harness, provider=True), errors.InvalidRequest)):
        await _refused(port.admit_credit(request, b_idem(request)), error)
    operator_key = harness.ids.uuid()
    harness.extra["register_credential"](v2.AuthContextV2(
        audience=v2.CredentialAudience.operator, org_id=IDS.consumer_org, key_id=operator_key,
        principal="operator@platform", role=v1.Role.operator, entitlement_version=1))
    operator = b.request(harness, org_id=IDS.consumer_org, key_id=operator_key,
                         model_revision=v2fix.REQUESTED_MODEL)
    await _refused(port.admit_credit(operator, b_idem(operator)), errors.Forbidden)
    # The dev deployment gets an approved internal card; the provider's dev wallet is
    # still zero, so the hold does not fit until an audited allocation funds it.
    harness.extra["publish_rate_card"](_card(rate_card_version="rc_marlin2b_dev_internal",
                                             deployment_revision_id=IDS.dev_deployment))
    dev = _credit_request(harness, provider=True)
    await _refused(port.admit_credit(dev, b_idem(dev)), errors.InsufficientCredit)
    assert harness.extra["active_jobs"]() == []
    assert harness.extra["credit_balance"](IDS.consumer_wallet)["reserved"] == reserved
    assert harness.extra["credit_balance"](IDS.provider_dev_wallet)["reserved"] == 0
    harness.extra["credit_grant"](IDS.provider_dev_wallet, "100")
    admitted = await port.admit_credit(dev, b_idem(dev))
    assert admitted.wallet_id == IDS.provider_dev_wallet
    assert admitted.pins.rate_card_version == "rc_marlin2b_dev_internal"
    assert harness.extra["credit_balance"](IDS.consumer_wallet)["reserved"] == reserved, \
        "a provider dev admission touched the consumer wallet"


async def credit_admit__a_replay_is_pinned_and_never_crosses_regimes(factory):
    """CREDIT-RATE/DUR-ADMIT: a replay after a rate change answers the admitted card
    and hold, marked replayed; the same key through the legacy `admit` is an
    idempotency conflict, never a USD admission of CREDIT money."""
    harness = factory()
    request = _credit_request(harness)
    admission = await harness.port.admit_credit(request, b_idem(request))
    harness.extra["publish_rate_card"](_card(rate_card_version="rc_marlin2b_2026_10",
                                             input_rate_per_million="800.00000000",
                                             output_rate_per_million="2400.00000000"))
    replay = await harness.port.admit_credit(request, b_idem(request))
    assert replay.replayed is True
    assert replay.model_dump(exclude={"replayed"}) == admission.model_dump(exclude={"replayed"})
    await _refused(harness.port.admit(request, b_idem(request)), errors.IdempotencyConflict)


async def credit_settle__at_the_admitted_card_on_the_credit_wallet_only(factory):
    """CREDIT-RATE/CREDIT-SPEND at settlement (R68): a rate published while the job
    ran does not reach it; the charge is the admitted card's half-up debit, taken from
    the CREDIT wallet with the hold released in the same transaction; the v1 debit
    field stays zero and the USD wallet does not move. The worker's view carries the
    admitted card and the resolved wallet."""
    from . import builders as b
    harness = factory()
    request = _credit_request(harness)
    before = harness.extra["credit_balance"](IDS.consumer_wallet)
    admission = await harness.port.admit_credit(request, b_idem(request))
    harness.extra["publish_rate_card"](_card(rate_card_version="rc_marlin2b_2026_10",
                                             input_rate_per_million="4000.00000000",
                                             output_rate_per_million="12000.00000000"))
    lease = await _credit_run(harness, request, admission)
    work = await harness.port.load_work_credit(lease)
    assert work.rate_card == admission.rate_card and work.request.pins == admission.pins
    assert work.request.wallet_id == admission.wallet_id and work.request.request == request
    await _refused(harness.port.load_work(lease), errors.NotFound)
    outcome, settlement = await harness.port.complete_credit(
        lease, b.outcome(request.request_id, harness, tokens=b.usage(1200, 340)))
    expected = admission.rate_card.debit(1200, 340)
    assert outcome.settlement_state is v1.SettlementState.settled
    assert outcome.debit == money.ZERO, "a CREDIT charge was written into the USD field"
    assert settlement is not None and settlement.charged == expected
    assert settlement.rate_card_version == v2fix.RATE_CARD_VERSION
    assert settlement.wallet_id == IDS.consumer_wallet
    after = harness.extra["credit_balance"](IDS.consumer_wallet)
    assert after["ledger"] == before["ledger"] - expected.raw(mu.CREDIT)
    assert after["reserved"] == before["reserved"]
    usd = harness.extra["balance"](IDS.consumer_org)
    assert usd["ledger"] == 0 and usd["reserved"] == 0


async def credit_settle__a_free_outcome_moves_no_credit(factory):
    """CREDIT-SPEND: a free cause (invalid media) settles nothing: no settlement
    record, the hold released, the ledger unchanged."""
    from . import builders as b
    harness = factory()
    request = _credit_request(harness)
    before = harness.extra["credit_balance"](IDS.consumer_wallet)
    admission = await harness.port.admit_credit(request, b_idem(request))
    lease = await _credit_run(harness, request, admission)
    outcome, settlement = await harness.port.complete_credit(
        lease, b.outcome(request.request_id, harness, cause=v1.TerminalCause.invalid_media,
                         state=v1.JobState.failed, tokens=None, result_ref=None))
    assert outcome.settlement_state is v1.SettlementState.released_free
    assert settlement is None
    assert harness.extra["credit_balance"](IDS.consumer_wallet) == before


async def credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet(factory):
    """CREDIT-SPEND / DUR-SETTLE: published output with no authoritative usage holds the
    CREDIT hold (`held_unknown`, no settlement); after the fenced 24 h the reaper
    releases it on that CREDIT wallet as platform-absorbed - never on the organization's
    USD wallet, and never as a charge."""
    from . import builders as b
    from .harness import hook
    from ..limits import DEFAULTS
    harness = factory()
    publish = hook(harness, "publish")
    request = _credit_request(harness)
    before = harness.extra["credit_balance"](IDS.consumer_wallet)
    admission = await harness.port.admit_credit(request, b_idem(request))
    lease = await _credit_run(harness, request, admission)
    await publish(lease)
    outcome, settlement = await harness.port.complete_credit(
        lease, b.outcome(request.request_id, harness, cause=v1.TerminalCause.client_disconnected,
                         state=v1.JobState.failed, tokens=None, result_ref=None))
    assert outcome.settlement_state is v1.SettlementState.held_unknown and settlement is None
    held = harness.extra["credit_balance"](IDS.consumer_wallet)
    assert held["reserved"] == before["reserved"] + admission.maximum_hold.raw(mu.CREDIT)
    harness.clock.advance(DEFAULTS.unknown_usage_reconcile_s + 1)
    await harness.port.recover()
    assert harness.extra["credit_balance"](IDS.consumer_wallet) == before, \
        "the reconcile did not return the CREDIT hold, or charged it"
    usd = harness.extra["balance"](IDS.consumer_org)
    assert usd["ledger"] == 0 and usd["reserved"] == 0, "the reconcile moved the USD wallet"
    _, final = await harness.port.get_owned_credit(IDS.consumer_org, admission.job_handle)
    assert final.settlement_state is v1.SettlementState.released_platform_absorbed


def b_idem(request, key: str = "credit-1"):
    from . import builders as b
    return b.idem(request, key)


def credit_jobstore_cases() -> list[Callable]:
    """The `ports.CreditJobStore` suite, in oracle order."""
    return [
        credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit,
        credit_admit__refusals_leave_no_job_and_no_hold,
        credit_admit__a_replay_is_pinned_and_never_crosses_regimes,
        credit_settle__at_the_admitted_card_on_the_credit_wallet_only,
        credit_settle__a_free_outcome_moves_no_credit,
        credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet,
    ]


def run_credit_jobstore_conformance(factory) -> int:
    """Run every CREDIT JobStore case against `factory() -> Harness`; raises on failure."""
    from . import run_cases
    return run_cases(credit_jobstore_cases(), factory)


def run_v2_conformance(factory: Callable[[], V2Harness] = fake_v2_harness) -> int:
    """Run every case against `factory`; return how many ran. Raises on failure."""
    ran = 0
    for case in cases():
        asyncio.run(case(factory))
        ran += 1
    return ran


__all__ = ["cases", "credit_jobstore_cases", "run_credit_jobstore_conformance",
           "run_v2_conformance", "fake_v2_harness", "V2Harness"]
