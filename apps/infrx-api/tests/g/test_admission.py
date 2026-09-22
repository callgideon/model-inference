#!/usr/bin/env python3
"""API-AUTH / CREDIT-RATE / DUR-CAP (G1R item 3): what the ingress hands admission, and
what admission does with it.

The ingress hands `accept` the credential's identity, the name the caller asked for and
the idempotency scope - never a wallet, a rate or a policy, and never its own resolution.
Admission (D2; here `credit.CreditStore`, its in-memory stand-in) re-reads the key,
resolves the wallet from the key's identity and re-resolves and pins the name in one
transaction. So a rate or alias change after acceptance moves nothing already admitted,
an idempotent replay returns the admission as it was, and every refusal - the ingress's
or the store's - leaves no job, hold, idempotency record or journal reservation.

Against the stand-in these cases are *implemented*, not integrated: D2's real
`PgJobStore.admit_credit` is the integration target.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from infrx.contracts import errors, wire
from infrx.contracts.conformance.v2_fakes import fake_v2_harness
from infrx.contracts.v2.money_units import Credit
from infrx.contracts.v2.records import DeploymentState, RateCardSnapshot

from . import credit, support

IDS = support.IDS
PROD = IDS.prod_deployment
NONE = ({}, {}, {}, {})                       # no job, hold, replay record or reservation
# The fixture consumer, whose wallet the seeded directories hold (10,000 CREDIT).
CONSUMER_ROW = {"id": IDS.consumer_key, "org_id": IDS.consumer_org, "revoked_at": None,
                "audience": "consumer", "user_id": IDS.consumer_user}


class World:
    """One app over one store, sharing the catalog and the key rows the identity source
    serves - so a change "between validation and admission" is a change both can see."""

    def __init__(self, row=CONSUMER_ROW, *, max_active=64, before=None, catalog=None):
        """`before(world)` runs between the ingress's validation and the admission."""
        self.row = dict(row)
        self.catalog = catalog or support.catalog()
        self.wallets = fake_v2_harness().wallets
        self.store = credit.CreditStore(catalog=self.catalog, wallets=self.wallets,
                                        keys={self.row["id"]: self.row}, max_active=max_active)
        app, _ = support.cutover_app(
            sb=support.supabase(rows=(self.row,)),
            ingress_deps=support.deps(accept=credit.acceptor(
                self.store, before and (lambda: before(self))),
                                      catalog=self.catalog))
        self.tc = TestClient(app)

    def chat(self, key=None, model=support.MODEL_REVISION, headers=None, **body):
        headers = {**support.AUTH, **({"Idempotency-Key": key} if key else {}), **(headers or {})}
        return self.tc.post(support.CHAT_PATH, headers=headers, json={
            "model": model, "messages": [{"role": "user", "content": "hi"}], **body})


def refused(response, status, code):
    assert response.status_code == status, response.text
    error = support.error_of(response)
    assert (error["code"], error["message"]) == (code, errors.MESSAGES[code]), error


def doubled(card, version="rc_doubled"):
    return card.model_copy(update={"rate_card_version": version,
                                   "input_rate_per_million": Credit("800"),
                                   "output_rate_per_million": Credit("2400")})


def second_deployment(catalog):
    """A new production revision B of the same serving version, with its own card."""
    b_id = "c0000005-0000-4000-8000-000000000005"
    catalog.deployments[b_id] = catalog.deployments[PROD].model_copy(
        update={"deployment_revision_id": b_id})
    catalog.rate_cards[b_id] = catalog.rate_cards[PROD].model_copy(
        update={"deployment_revision_id": b_id, "rate_card_version": "rc_b"})
    catalog.policies[b_id] = catalog.policies[PROD]
    return b_id


# --- the trusted context ----------------------------------------------------------
FORGED = ("wallet_id", "wallet", "user", "user_id", "org_id", "provider_org_id", "endpoint_id",
          "rate_card", "rate_card_version", "input_rate_per_million", "price_snapshot",
          "policy_version", "serving_version_id", "deployment_revision_id",
          "accounting_regime", "metadata")


def test_api_auth__no_body_field_or_header_can_name_a_wallet_rate_or_policy():
    """Every identity, price and policy field a caller might try is refused by name (the
    closed parameter set); headers naming them are not read at all, so the admitted
    wallet is the key's own individual's and the pins are the catalog's."""
    world = World()
    for name in FORGED:
        response = world.chat(**{name: IDS.other_wallet})
        refused(response, 400, "unsupported_parameter")
        assert support.error_of(response)["param"] == name
    assert world.store.side_effects() == NONE
    response = world.chat(headers={"X-Wallet-Id": IDS.other_wallet,
                                   "OpenAI-Organization": IDS.other_org,
                                   "X-Org-Id": IDS.other_org, "X-User-Id": IDS.other_user,
                                   "X-Provider-Org-Id": IDS.provider_org,
                                   "X-Endpoint-Id": IDS.dev_endpoint})
    assert response.status_code == 202, response.text
    admission = response.json()
    assert (admission["wallet_id"], admission["org_id"]) == (IDS.consumer_wallet,
                                                             IDS.consumer_org)
    assert admission["pins"]["deployment_revision_id"] == PROD


def test_api_auth__a_provider_dev_key_never_spends_a_consumer_wallet():
    """Its own zero-initialised dev wallet or nothing: unfunded it is 402 with no
    reservation, and the consumer's wallet is never the fallback. Funded by an operator
    allocation, the same request is admitted against the provider wallet."""
    catalog = support.catalog()
    catalog.publish(support.preview_card())
    world = World(support.PROVIDER_ROW, catalog=catalog)
    refused(world.chat(model=support.DEV_MODEL), 402, "insufficient_credit")
    assert world.store.side_effects() == NONE
    wallet = world.wallets.by_provider[IDS.provider_org]
    world.wallets.by_provider[IDS.provider_org] = wallet.model_copy(
        update={"ledger_total": Credit("100")})
    response = world.chat(model=support.DEV_MODEL)
    assert response.status_code == 202, response.text
    assert response.json()["wallet_id"] == IDS.provider_dev_wallet
    assert [owner for owner, _ in world.store.holds.values()] == [IDS.provider_dev_wallet]


# --- CREDIT-RATE: accepted jobs and replays keep their pins -----------------------
def test_credit_rate__a_rate_published_after_acceptance_moves_no_admitted_job_or_replay():
    world = World()
    first = world.chat(key="k1")
    assert first.status_code == 202, first.text
    world.catalog.publish(doubled(world.catalog.rate_cards[PROD]))
    replay = world.chat(key="k1")
    assert replay.status_code == 202 and replay.headers[wire.HEADER_IDEMPOTENCY_REPLAYED] == "true"
    for field in ("job_handle", "pins", "rate_card", "maximum_hold", "wallet_id"):
        assert replay.json()[field] == first.json()[field], field
    fresh = world.chat(key="k2")
    assert fresh.json()["pins"]["rate_card_version"] == "rc_doubled"
    assert Credit(fresh.json()["maximum_hold"]) > Credit(first.json()["maximum_hold"])
    assert len(world.store.jobs) == 2


def test_credit_rate__an_alias_moved_after_acceptance_moves_no_admitted_job_or_replay():
    world = World()
    first = world.chat(key="k1", model=support.PUBLIC_MODEL)
    assert first.json()["pins"]["deployment_revision_id"] == PROD
    b_id = second_deployment(world.catalog)
    world.catalog.move_alias(support.PUBLIC_MODEL, b_id)
    replay = world.chat(key="k1", model=support.PUBLIC_MODEL)
    assert replay.json()["pins"] == first.json()["pins"]
    fresh = world.chat(key="k2", model=support.PUBLIC_MODEL)
    assert (fresh.json()["pins"]["deployment_revision_id"],
            fresh.json()["pins"]["rate_card_version"]) == (b_id, "rc_b")


def test_credit_rate__admission_takes_the_publication_current_at_admission():
    """The race: the ingress validated against A, then the alias moved (or a rate was
    published) before the store's transaction. The job is admitted at what the store
    read - the ingress handed it the name, not its own resolution."""
    catalog = support.catalog()
    b_id = second_deployment(catalog)
    world = World(catalog=catalog,
                  before=lambda w: w.catalog.move_alias(support.PUBLIC_MODEL, b_id))
    admitted = world.chat(model=support.PUBLIC_MODEL).json()
    assert (admitted["pins"]["deployment_revision_id"], admitted["rate_card"]["rate_card_version"]
            ) == (b_id, "rc_b")
    assert admitted["pins"]["requested_model"] == support.PUBLIC_MODEL
    catalog = support.catalog()
    world = World(catalog=catalog,
                  before=lambda w: w.catalog.publish(doubled(w.catalog.rate_cards[PROD])))
    admitted = world.chat().json()
    assert admitted["pins"]["rate_card_version"] == admitted["rate_card"]["rate_card_version"] \
        == "rc_doubled"


# --- denied capacity, and the retry ---------------------------------------------------
def test_dur_cap__denied_capacity_is_retryable_and_the_retry_is_admitted_once():
    """429 with retry guidance and nothing written; once capacity frees, the same key is
    admitted exactly once, and a further retry replays that one admission."""
    world = World(max_active=1)
    first = world.chat(key="k1")
    assert first.status_code == 202, first.text
    before = world.store.side_effects()
    denied = world.chat(key="k2")
    refused(denied, 429, "capacity_exhausted")
    assert denied.headers[wire.HEADER_RETRY_AFTER] == "5"
    assert world.store.side_effects() == before
    world.store.finish(first.json()["request_id"])
    retried = world.chat(key="k2")
    assert retried.status_code == 202 and wire.HEADER_IDEMPOTENCY_REPLAYED not in retried.headers
    again = world.chat(key="k2")
    assert again.headers[wire.HEADER_IDEMPOTENCY_REPLAYED] == "true"
    assert again.json()["job_handle"] == retried.json()["job_handle"]
    assert len(world.store.jobs) == 2 and len(world.store.holds) == 2


# --- the verification list: every refusal leaves nothing behind -----------------------
def _retire(world):
    deployments = world.catalog.deployments
    deployments[PROD] = deployments[PROD].model_copy(update={"state": DeploymentState.retired})


def _usd_unit(catalog):
    card = catalog.rate_cards[PROD]
    catalog.rate_cards[PROD] = RateCardSnapshot.model_construct(**{**dict(card), "unit": "USD"})


REVOKED = "2026-09-22T11:00:00Z"
REFUSALS = (
    ("forged wallet id", {"body": {"wallet_id": IDS.other_wallet}}, 400, "unsupported_parameter"),
    ("forged provider id", {"body": {"provider_org_id": IDS.rival_provider_org}}, 400,
     "unsupported_parameter"),
    ("private endpoint", {"model": support.DEV_MODEL}, 404, "not_found"),
    ("revoked publication", {"catalog": lambda c: c.aliases.pop(support.MODEL_REVISION)},
     404, "not_found"),
    ("publication revoked during admission", {"before": _retire}, 404, "not_found"),
    ("revoked key", {"row": {**CONSUMER_ROW, "revoked_at": REVOKED}}, 401, "invalid_api_key"),
    ("key revoked after the ingress read it",
     {"before": lambda world: world.row.__setitem__("revoked_at", REVOKED)}, 401,
     "invalid_api_key"),
    ("missing rate", {"catalog": lambda c: c.rate_cards.pop(PROD)}, 400, "invalid_request"),
    ("wrong unit", {"catalog": _usd_unit}, 400, "invalid_request"),
    ("operator credential", {"row": {**support.OPERATOR_ROW}}, 403, "forbidden"),
    ("unfunded provider wallet", {"row": support.PROVIDER_ROW, "model": support.DEV_MODEL,
                                  "catalog": lambda c: c.publish(support.preview_card())},
     402, "insufficient_credit"),
    ("denied capacity", {"max_active": 0}, 429, "capacity_exhausted"),
)


@pytest.mark.parametrize("name,case,status,code", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_api_auth__refusals_leave_no_hold_job_or_journal_reservation(name, case, status, code):
    catalog = support.catalog()
    if "catalog" in case:
        case["catalog"](catalog)
    world = World(case.get("row", CONSUMER_ROW), max_active=case.get("max_active", 64),
                  before=case.get("before"), catalog=catalog)
    wallet = world.wallets.by_user[IDS.consumer_user]
    refused(world.chat(key="k1", model=case.get("model", support.MODEL_REVISION),
                       **case.get("body", {})), status, code)
    assert world.store.side_effects() == NONE
    assert world.store.available(wallet) == wallet.available
