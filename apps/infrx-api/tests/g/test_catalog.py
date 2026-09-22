#!/usr/bin/env python3
"""SPLIT-CONTRACT / CREDIT-RATE / API-AUTH (G1R item 2): a model name resolves, for the
credential's audience, only to an approved, published, priced deployment, and a request
is accepted only for capabilities its serving revision declares.

Every refusal is the contract envelope, and nothing reaches `accept`: a refusal here
happens before any durable state exists, so it cannot leave a hold, a job or a journal
reservation behind.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from infrx.contracts import errors, wire
from infrx.contracts.conformance import builders as b
from infrx.contracts.conformance.v2_fakes import FakeCatalogDirectory
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.money_units import Usd
from infrx.contracts.v2.records import CredentialAudience, DeploymentState, RateCardSnapshot

from . import support

IDS = support.IDS
PROD = IDS.prod_deployment
TEXT = {"role": "user", "content": "describe"}
VIDEO = {"role": "user", "content": [{"type": "text", "text": "describe"},
                                     {"type": "video_url",
                                      "video_url": {"url": "https://cdn.test/a.mp4"}}]}


def app_with(row=support.ROW, catalog=None):
    calls, accept = support.recorder()
    app, _ = support.cutover_app(sb=support.supabase(rows=(row,)),
                                 ingress_deps=support.deps(accept=accept,
                                                           catalog=catalog or support.catalog()))
    return TestClient(app), calls


def chat(tc, model=support.MODEL_REVISION, message=TEXT, **extra):
    return tc.post(support.CHAT_PATH, headers=support.AUTH,
                   json={"model": model, "messages": [message], **extra})


def refused(response, status, code):
    assert response.status_code == status, response.text
    error = support.error_of(response)
    assert error["code"] == code, error
    assert error["message"] == errors.MESSAGES[code]          # fixed, never the detail
    return error


def replaced(catalog, deployment_id=PROD, **update):
    catalog.deployments[deployment_id] = catalog.deployments[deployment_id].model_copy(
        update=update)
    return catalog


def with_preview_card():
    catalog = support.catalog()
    catalog.publish(support.preview_card())
    return catalog


def leaky(catalog):
    """A catalog that answers every alias to every audience - what an adapter that forgot
    07's visibility rule would do. The ingress's own audience rule must still hold."""
    async def resolve(requested_model, *, audience, endpoint_id):
        return catalog.deployments.get(catalog.aliases.get(requested_model, ""))

    catalog.resolve = resolve
    return catalog


# --- audiences --------------------------------------------------------------------
def test_split_contract__a_consumer_key_cannot_reach_a_private_dev_endpoint():
    """R70: not_found, the same answer as a name nobody published - never a 403 that
    confirms the artifact - even when the dev deployment is priced and the catalog
    hands its row to anyone."""
    tc, calls = app_with(catalog=leaky(with_preview_card()))
    private = chat(tc, model=support.DEV_MODEL)
    unknown = chat(tc, model="nemostation/nothing@2026-09-01")
    for response in (private, unknown):
        refused(response, 404, "not_found")
    strip = lambda r: {k: v for k, v in support.error_of(r).items() if k != "request_id"}  # noqa
    assert strip(private) == strip(unknown)
    assert calls == []


def test_split_contract__a_provider_dev_key_reaches_only_its_own_private_endpoint():
    """Its own ready private endpoint, and nothing else: not the production listing
    (that would spend a provider dev wallet on a consumer model), not another endpoint,
    not another provider's."""
    tc, calls = app_with(support.PROVIDER_ROW, with_preview_card())
    assert chat(tc, model=support.DEV_MODEL).status_code == 202
    auth = calls[0][0]
    assert (auth.audience, auth.provider_org_id, auth.endpoint_id) == (
        CredentialAudience.provider_dev, IDS.provider_org, IDS.dev_endpoint)
    refused(chat(tc, model=support.MODEL_REVISION), 404, "not_found")      # production
    for scope in ({"endpoint_id": IDS.prod_endpoint},
                  {"provider_org_id": IDS.rival_provider_org}):
        tc, calls = app_with({**support.PROVIDER_ROW, **scope}, with_preview_card())
        refused(chat(tc, model=support.DEV_MODEL), 404, "not_found")
        assert calls == []


def test_split_contract__an_operator_key_runs_no_inference():
    """R66: an operator credential spends no wallet, so it has nothing to run inference
    on. It still authenticates (readiness answers it)."""
    tc, calls = app_with(support.OPERATOR_ROW)
    refused(chat(tc), 403, "forbidden")
    assert calls == []
    assert tc.get(support.READY_PATH, headers=support.AUTH).status_code == 200


# --- publication ------------------------------------------------------------------
UNPUBLISHED = (("proposed", DeploymentState.proposed_public),
               ("draining", DeploymentState.draining),
               ("retired", DeploymentState.retired))


@pytest.mark.parametrize("name,state", UNPUBLISHED, ids=[u[0] for u in UNPUBLISHED])
def test_credit_rate__only_an_active_published_deployment_is_callable(name, state):
    """A public alias reaches an `active` deployment only (D1R's resolver reads the same
    rule); proposed, draining and retired revisions are not_found, like a revoked alias."""
    tc, calls = app_with(catalog=replaced(support.catalog(), state=state))
    refused(chat(tc), 404, "not_found")
    assert calls == []


def test_credit_rate__a_revoked_publication_is_not_found():
    """The alias withdrawn, or the private endpoint not yet validated: nothing to call."""
    catalog = support.catalog()
    del catalog.aliases[support.MODEL_REVISION]
    tc, calls = app_with(catalog=catalog)
    refused(chat(tc), 404, "not_found")
    for state in (DeploymentState.draft, DeploymentState.validating):
        tc, calls = app_with(support.PROVIDER_ROW,
                             replaced(with_preview_card(), IDS.dev_deployment, state=state))
        refused(chat(tc, model=support.DEV_MODEL), 404, "not_found")
    assert calls == []


def test_split_contract__the_operator_seeded_catalog_serves_without_lab():
    """The four rows D1R's operator seed writes (the F2P fixtures verbatim) are the whole
    catalog: no provider membership, no access grant, no Lab process."""
    built = {name: v2fix.BUILDERS[name]() for name in (
        "deployment_revision_public.json", "serving_revision.json", "rate_card_marlin.json",
        "data_access_policy.json")}
    prod = built["deployment_revision_public.json"]
    seeded = FakeCatalogDirectory(
        aliases={v2fix.REQUESTED_MODEL: prod.deployment_revision_id},
        deployments={prod.deployment_revision_id: prod},
        servings={IDS.serving_version: built["serving_revision.json"]},
        rate_cards={prod.deployment_revision_id: built["rate_card_marlin.json"]},
        policies={prod.deployment_revision_id: built["data_access_policy.json"]})
    tc, calls = app_with(catalog=seeded)
    assert chat(tc, model=v2fix.REQUESTED_MODEL, message=VIDEO).status_code == 202
    assert calls[0][1].model_revision == v2fix.REQUESTED_MODEL


# --- price ------------------------------------------------------------------------
def _usd_rates(card):
    return RateCardSnapshot.model_construct(**{**dict(card), "input_rate_per_million": Usd("400"),
                                               "output_rate_per_million": Usd("1200")})


UNPRICED = (
    ("no card at all", lambda card: None),
    ("a USD unit", lambda card: RateCardSnapshot.model_construct(**{**dict(card), "unit": "USD"})),
    ("USD amounts", _usd_rates),
    ("a v1 price snapshot", lambda card: b.DEFAULT_PRICE),
    ("another deployment's card",
     lambda card: card.model_copy(update={"deployment_revision_id": IDS.dev_deployment})),
)


@pytest.mark.parametrize("name,card", UNPRICED, ids=[u[0] for u in UNPRICED])
def test_credit_rate__an_unpriced_or_wrong_unit_model_is_refused(name, card):
    """R69: unpriced is unserveable, never free. R64: a unit is a type, so a card whose
    unit or amounts are not CREDIT is no card at all."""
    catalog = support.catalog()
    catalog.rate_cards[PROD] = card(catalog.rate_cards[PROD])
    tc, calls = app_with(catalog=catalog)
    refused(chat(tc), 400, "invalid_request")
    assert calls == []


def test_credit_rate__a_provider_preview_needs_its_own_approved_card():
    """The dev deployment the fixtures leave unpriced: an operator-funded preview is
    still refused until an internal card is approved for it."""
    tc, calls = app_with(support.PROVIDER_ROW)
    refused(chat(tc, model=support.DEV_MODEL), 400, "invalid_request")
    assert calls == []


# --- capability and ceilings ------------------------------------------------------
def _serving(catalog, **capability):
    serving = catalog.servings[IDS.serving_version]
    catalog.servings[IDS.serving_version] = serving.model_copy(update={
        "capability": serving.capability.model_copy(update=capability)})
    return catalog


def test_split_contract__only_declared_capabilities_are_accepted():
    """07's capability record decides: video for a text-only revision is
    unsupported_media, a stream from one that does not stream is unsupported_parameter."""
    tc, calls = app_with(catalog=_serving(support.catalog(), input_modalities=("text",)))
    assert refused(chat(tc, message=VIDEO), 400, "unsupported_media")["param"] == "messages"
    assert chat(tc, message=TEXT).status_code == 202
    tc, calls = app_with(catalog=_serving(support.catalog(), input_modalities=("video",)))
    refused(chat(tc, message=VIDEO), 400, "unsupported_media")     # its text part
    tc, calls = app_with(catalog=_serving(support.catalog(), stream_output=False))
    assert refused(chat(tc, stream=True), 400, "unsupported_parameter")["param"] == "stream"
    assert chat(tc).status_code == 202
    assert len(calls) == 1


def test_split_contract__the_ceilings_are_the_deployments_own():
    """The output ceiling is the smaller of the pilot's and the deployment's; the input
    ceiling the context minus the output, never more than the deployment accepts (D2
    refuses past it)."""
    catalog = replaced(support.catalog(), max_input_tokens=32_768, max_output_tokens=1_024)
    tc, calls = app_with(catalog=catalog)
    assert chat(tc).status_code == 202
    assert (calls[-1][1].max_output_tokens, calls[-1][1].max_input_tokens) == (1_024, 31_744)
    assert chat(tc, max_tokens=512).status_code == 202
    assert (calls[-1][1].max_output_tokens, calls[-1][1].max_input_tokens) == (512, 32_256)
    assert refused(chat(tc, max_tokens=2_048), 400, "invalid_request")["param"] == "max_tokens"
    assert len(calls) == 2


def test_split_contract__a_catalog_outage_is_retryable_not_a_missing_model(caplog):
    """A lookup that failed is not a model that does not exist: 503 with retry guidance,
    and the driver's text stays in the log."""
    catalog = support.catalog()

    async def down(*_args, **_kw):
        raise ConnectionError("postgresql://infrx:pw@db/infrx is unreachable")

    catalog.resolve = down
    tc, calls = app_with(catalog=catalog)
    with caplog.at_level("ERROR", logger="infrx.gateway"):
        response = chat(tc)
    refused(response, 503, "dependency_unavailable")
    assert response.headers[wire.HEADER_RETRY_AFTER]
    assert "pw@db" not in response.text and "pw@db" in caplog.text
    assert calls == []
