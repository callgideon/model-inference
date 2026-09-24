#!/usr/bin/env python3
"""CATALOG-TRUTH / CREDIT-RATE (G7 item 2, P-22, S3 F11): one canonical public ID, its
aliases, and one rate identity - the same for discovery, admission and accounting.

* Every spelling of F2C.c's compatibility table (`published/alias_compatibility.json`,
  every model string in code, fixtures and results) is admitted exactly when discovery
  lists it, with the pins the table records, and refused `not_found` before any durable
  work otherwise. The ingress hands admission the caller's spelling verbatim; admission
  re-resolves and pins (D2, R78) - never a rewrite.
* A card this deployment was not approved to serve is refused BEFORE admission (no job,
  no hold): the relay's post-admission recheck stays as the defence for an alias moved in
  between. A job pinned to an earlier card keeps answering (history, F11).
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts.conformance.v2_fakes import fake_v2_harness
from infrx.contracts.v2 import published_fixtures
from infrx.gateway.routes import ingress, models

from . import credit, relay_support as rs, support
from .jobs import world as jw
from .test_admission import CONSUMER_ROW
from .test_catalog_truth import DEPLOYED, USD, priced

IDS = support.IDS
CARD = "rc_marlin2b_2026_09_provisional"
CREDIT_BUILD = support.BUILD.replace(accounting_regime="credit")
TABLE = json.loads((published_fixtures.DIR / "alias_compatibility.json").read_text())["cases"]


def credit_settings(card=CARD):
    return support.settings(deployment=CREDIT_BUILD, active_rate_card_version=card, **DEPLOYED)


class Priced:
    """Discovery and the metered ingress on one app, over D2's CREDIT admission stand-in
    (`credit.CreditStore`) and one catalog."""

    def __init__(self, config, catalog=None):
        self.catalog = priced(catalog)
        self.store = credit.CreditStore(catalog=self.catalog, wallets=fake_v2_harness().wallets,
                                        keys={CONSUMER_ROW["id"]: CONSUMER_ROW})
        rt = support.runtime(config, sb=support.supabase(rows=(CONSUMER_ROW,)))
        rt.ingress = support.deps(accept=credit.acceptor(self.store), catalog=self.catalog)
        app = FastAPI()
        app.state.runtime, rt.app = rt, app
        models.register(app, rt)
        ingress.register(app, rt)
        self.tc = TestClient(app)

    def aliases(self) -> set[str]:
        return {alias for entry in self.tc.get("/v1/models").json()["data"]
                for alias in entry["aliases"]}

    def chat(self, model=None, **body):
        return self.tc.post(support.CHAT_PATH, headers=support.AUTH, json={
            **({"model": model} if model is not None else {}),
            "messages": [{"role": "user", "content": "hi"}], **body})


def test_alias_pricing__every_known_spelling_resolves_and_prices_as_the_table():
    """P-22's compatibility, per spelling: listed by discovery <=> admitted, with the
    canonical deployment/serving revision and the one CREDIT identity the table records,
    and the caller's spelling kept verbatim; every other spelling is `not_found` with
    nothing admitted. An omitted model is the served default (the bare alias)."""
    world = Priced(credit_settings())
    listed = world.aliases()
    for case in TABLE:
        spelling, expected = case["requested"], case["credit"]
        before = world.store.side_effects()
        response = world.chat(spelling)
        if "refused" in expected:
            assert response.status_code == 404, (spelling, response.text)
            assert support.error_of(response)["code"] == expected["refused"]
            assert world.store.side_effects() == before, spelling
            assert spelling not in listed, spelling
            continue
        assert response.status_code == 202, (spelling, response.text)
        assert spelling in listed, spelling
        pins = response.json()["pins"]
        assert pins["requested_model"] == spelling == expected["requested_model"]
        assert (pins["deployment_revision_id"], pins["serving_version_id"],
                pins["rate_card_version"]) == (expected["deployment_revision_id"],
                                               expected["serving_version_id"],
                                               expected["rate_card_version"])
    omitted = world.chat()
    assert omitted.status_code == 202, omitted.text
    assert omitted.json()["pins"]["requested_model"] == TABLE[0]["requested"]


def test_alias_pricing__legacy_discovery_prices_every_alias_by_the_canonical_revision():
    """The legacy half: discovery's one USD identity is keyed by the canonical revision
    (never a spelling), the identity the table says each resolving spelling prices at,
    and the ingress hands admission the spelling verbatim (the store resolves, D10)."""
    calls, accept = support.recorder()
    rt = support.runtime(support.settings(**DEPLOYED))
    rt.ingress = support.deps(accept=accept, catalog=priced(snapshot=USD.model_copy(update={
        "price_version": TABLE[1]["legacy_usd"]["price_version"]})))
    app = FastAPI()
    app.state.runtime, rt.app = rt, app
    models.register(app, rt)
    ingress.register(app, rt)
    tc = TestClient(app)
    (entry,) = tc.get("/v1/models").json()["data"]
    usd = entry["pricing"]["legacy_usd"]
    for case in TABLE:
        expected = case["legacy_usd"]
        if "refused" in expected:
            continue
        assert case["requested"] in entry["aliases"]
        assert (usd["price_version"], usd["model_revision"]) == (
            expected["price_version"], expected["model_revision"])
        response = tc.post(support.CHAT_PATH, headers=support.AUTH, json={
            "model": case["requested"], "messages": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 202, response.text
        assert calls[-1][1].model_revision == case["requested"]      # verbatim, never rewritten


def test_alias_pricing__an_unapproved_card_is_refused_before_any_hold():
    """S3 F11: an operator published another card for the deployment, but this gateway is
    approved to serve `ACTIVE_RATE_CARD_VERSION` only. A new request is `invalid_request`
    at the ingress: no job, no hold, no idempotency record, no reservation - where the
    relay's recheck used to refuse it only after admission had held CREDIT."""
    world = Priced(credit_settings())
    world.catalog.publish(world.catalog.rate_cards[IDS.prod_deployment].model_copy(
        update={"rate_card_version": "rc_published_not_approved"}))
    response = world.chat(support.MODEL_REVISION)
    assert response.status_code == 400, response.text
    assert support.error_of(response)["code"] == "invalid_request"
    assert world.store.side_effects() == ({}, {}, {}, {})
    assert world.aliases() == set()                   # and discovery lists nothing either


def test_alias_pricing__a_job_on_the_earlier_card_keeps_answering(monkeypatch):
    """History (F11): a job admitted on card A replays by its key, and its status reads,
    after the deployment moved to card B - both provisional ids stay valid for what they
    priced - while a new request is admitted on B."""
    world = jw.JobsWorld(regime=rs.CREDIT, config=credit_settings())
    first = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(), key="k-a"))
    assert first.status == 202, first.body
    handle = first.json()["job_handle"]
    card_b = world.catalog.rate_cards[IDS.prod_deployment].model_copy(
        update={"rate_card_version": "rc_marlin2b_20260901T000000Z_provisional_p01"})
    world.catalog.publish(card_b)
    world.card, world.config = card_b.rate_card_version, credit_settings(card_b.rate_card_version)
    world.restart()
    again = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(), key="k-a"))
    assert again.status == 202, again.body
    assert again.json()["job_handle"] == handle
    assert again.headers["idempotency-replayed"] == "true"
    status = rs.run(jw.send(world.app, "GET", jw.job_path(handle)))
    assert status.status == 200, status.body
    fresh = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(stop="b"), key="k-b"))
    assert fresh.status == 202, fresh.body
    pinned = {job.credit.pins.rate_card_version for job in world.jobs.jobs.values()}
    assert pinned == {CARD, card_b.rate_card_version}
