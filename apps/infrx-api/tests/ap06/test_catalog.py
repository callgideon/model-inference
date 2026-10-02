"""AP-06 06c/06d: `GET /v1/models` derives every listing from the registry - and the served
Marlin document stays byte for byte what release b05eb6f4 serves.

`fixtures/models-document-b05eb6f4.json` is the response body G7's discovery app
(`tests/g/test_catalog_truth.discovery_app`) returned at the wave base, captured before this
lane changed `models.py`, in both regimes. A second alias is the served Marlin's rows under a
new identity (`Second`): listed only with an operator-approved card AND a route the gateway
can send it to (`rt.routes`, AP-05's endpoints: wiring); a down route is `unavailable`.
"""
from __future__ import annotations

import json
import pathlib

from infrx.contracts.v2.records import CredentialAudience

from tests.g import support as g
from tests.g import test_catalog_truth as ct

GOLDEN = pathlib.Path(__file__).with_name("fixtures") / "models-document-b05eb6f4.json"
OTHER = "acme/other-2b"
DEP, SRV = "a6000000-0000-4000-8000-000000000001", "a6000000-0000-4000-8000-000000000002"


def credit():
    return g.settings(deployment=ct.CREDIT, active_rate_card_version=ct.CARD, **ct.DEPLOYED)


class Second:
    """The seeded catalog plus `OTHER`: Marlin's rows under new ids and alias, with a card
    `approved_by` the given principal, listed by `published_aliases`."""

    def __init__(self, approved_by="operator:ops-1", visible=True) -> None:
        self.base, self.approved_by, self.visible = ct.priced(), approved_by, visible

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def published_aliases(self):
        return ["nemostation/marlin-2b", OTHER]

    async def _marlin(self):
        d = await self.base.resolve("nemostation/marlin-2b",
                                    audience=CredentialAudience.consumer, endpoint_id=None)
        return d, await self.base.serving_revision(d.serving_version_id), \
            await self.base.active_rate_card(d.deployment_revision_id)

    async def resolve(self, model, *, audience, endpoint_id):
        if model != OTHER:
            return await self.base.resolve(model, audience=audience, endpoint_id=endpoint_id)
        d, _, _ = await self._marlin()
        return d.model_copy(update={"deployment_revision_id": DEP, "serving_version_id": SRV}) \
            if self.visible else None

    async def serving_revision(self, serving_version_id):
        if serving_version_id != SRV:
            return await self.base.serving_revision(serving_version_id)
        _, s, _ = await self._marlin()
        return s.model_copy(update={"serving_version_id": SRV, "public_model_id": OTHER})

    async def active_rate_card(self, deployment_revision_id):
        if deployment_revision_id != DEP:
            return await self.base.active_rate_card(deployment_revision_id)
        _, _, card = await self._marlin()
        return card.model_copy(update={"deployment_revision_id": DEP, "serving_version_id": SRV,
                                       "rate_card_version": "rc_other",
                                       "approved_by": self.approved_by})

    async def listing_version(self, deployment_revision_id):
        return 1 if deployment_revision_id == DEP else \
            await self.base.listing_version(deployment_revision_id)


class Routes:
    def __init__(self, state="ready") -> None:
        self.state_, self.seen = state, []

    async def state(self, deployment, serving):
        self.seen.append((deployment.deployment_revision_id, serving.serving_version_id))
        if self.state_ == "raise":
            raise ConnectionError("route table down")
        return self.state_


def ids(catalog, routes=None, config=None):
    tc, _ = ct.discovery_app(config or credit(), catalog=catalog)
    if routes is not None:
        tc.app.state.runtime.routes = routes
    return {e["id"]: e for e in ct.listed(tc)}


def test_catalog__the_served_marlin_document_is_byte_identical_to_b05eb6f4():
    """Oracle: in both regimes the status and the exact response bytes equal the document
    captured at the wave base - the alias, the card and every field unchanged."""
    golden = json.loads(GOLDEN.read_text())
    for name, config in (("legacy", g.settings(**ct.DEPLOYED)), ("credit", credit())):
        tc, _ = ct.discovery_app(config)
        response = tc.get("/v1/models")
        assert (response.status_code, response.content.decode()) == (
            golden[name]["status"], golden[name]["body"]), name


def test_catalog__another_alias_is_hidden_without_a_route():
    """Oracle: a second published, priced, approved alias with no route table is not listed
    (the relay's one engine serves Marlin only); Marlin's entry is unchanged."""
    listed = ids(Second())
    assert list(listed) == ["nemostation/marlin-2b"]


def test_catalog__a_routed_approved_alias_is_listed_and_a_down_route_is_unavailable():
    """Oracle: a ready route lists the alias, owned by its provider slug, at its own card and
    available; the route was asked for exactly the resolved deployment and serving revision;
    a route that is down or cannot answer lists it explicitly `unavailable`."""
    routes = Routes("ready")
    listed = ids(Second(), routes)
    assert set(listed) == {"nemostation/marlin-2b", OTHER}
    other = listed[OTHER]
    assert (other["owned_by"], other["availability"]) == ("acme", "available")
    assert other["pricing"]["credit"]["rate_card_version"] == "rc_other"
    assert routes.seen == [(DEP, SRV)]
    for state in ("unavailable", "raise"):
        assert ids(Second(), Routes(state)).get(OTHER, {}).get("availability") \
            == "unavailable", state
    assert ids(Second(), Routes("ready"))["nemostation/marlin-2b"] == \
        ids(Second())["nemostation/marlin-2b"]


def test_catalog__a_provisional_card_or_a_private_revision_is_never_listed():
    """Oracle: a provisional card (no operator approval) or an alias a consumer key does not
    resolve (private, retired, unpublished) is hidden even with a ready route."""
    assert OTHER not in ids(Second(approved_by="provisional (P-01 pending)"), Routes())
    assert OTHER not in ids(Second(visible=False), Routes())
