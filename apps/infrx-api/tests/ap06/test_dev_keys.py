"""AP-06 06a (API-PUBLISH, private endpoint): provider_dev credentials and the dev wallet over
`/lab/v1/control`, on L3's fake world. The issued key is checked where it is used - A3's
catalog resolution, the admission door (`catalog.resolve`) - not only in its stored row.
"""
from __future__ import annotations

import pytest

from infrx.auth.context import auth_context
from infrx.contracts import errors
from infrx.gateway.routes import catalog
from infrx.operations.service import hash_key

from .support import ALIAS, IDS, Api, FakeCredentials, ready_dev, run, session

K = "/lab/v1/control/endpoints/{}/keys"


def issue(a, w, d, *, actor=None, key="key-dev-0001", name="ci"):
    a.as_(actor or session(w.DEV_A))
    return a.post(K.format(d.endpoint_id), {"name": name}, key=key, provider_org_id=w.A)


def test_dev_keys__a_developer_issues_an_endpoint_scoped_key_once(fake_world):
    """Oracle: 201 with the secret once; the stored row is the provider's, on this dev
    endpoint, holding the secret's hash; the same key replays 200 with the same key id and
    no secret; one key row exists."""
    w = fake_world
    _, d = ready_dev(w)
    a = Api(w)
    first = issue(a, w, d)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["secret_returned"] is True and body["secret"].startswith(body["prefix"])
    assert w.key_scope(body["key_id"]) == (w.A, d.endpoint_id, hash_key(body["secret"]))
    again = issue(a, w, d)
    assert again.status_code == 200
    assert again.json() == {**body, "secret": None, "secret_returned": False, "replayed": True}
    assert len(w.control_store.keys) == 1


def test_dev_keys__only_a_member_developer_issues_and_a_stranger_claims_no_key_scope(
        fake_world):
    """Oracle: a viewer is refused, another provider's developer gets not_found; neither
    leaves a receipt in A's scope, so A's developer using the same Idempotency-Key gets a
    fresh key, not a replayed refusal."""
    w = fake_world
    _, d = ready_dev(w)
    a = Api(w)
    assert issue(a, w, d, actor=session(w.VIEWER_A), key="key-shared-1").status_code == 403
    assert issue(a, w, d, actor=session(w.DEV_B), key="key-shared-1").status_code == 404
    assert not w.control_store.keys
    assert issue(a, w, d, key="key-shared-1").status_code == 201


def test_dev_keys__an_endpoint_without_a_validated_revision_has_no_key(fake_world):
    """Oracle: a draft-only dev endpoint and the public prod endpoint are 404."""
    w = fake_world
    s, d = ready_dev(w)
    draft = run(w.control.create_dev(w.DEV_A, w.A, serving_version_id=s.serving_version_id,
                                     endpoint_name="draft-only", max_input_tokens=1,
                                     max_output_tokens=1))
    a = Api(w)
    assert issue(a, w, draft).status_code == 404
    prod = run(w.control_store.deployment(IDS.prod_deployment))
    assert issue(a, w, prod, key="key-dev-prod").status_code == 404
    assert not w.control_store.keys


def test_dev_keys__an_api_key_is_not_a_lab_session(fake_world):
    """Oracle: a consumer or provider_dev key actor at the Lab door is 401, before anything."""
    w = fake_world
    _, d = ready_dev(w)
    a = Api(w)
    for audience in ("consumer", "provider_dev"):
        r = issue(a, w, d, actor=session(w.DEV_A, audience=audience))
        assert r.status_code == 401, r.text
    assert not w.control_store.keys


def test_dev_keys__the_issued_key_calls_only_its_private_endpoint(fake_world):
    """Oracle (private endpoint): once an operator priced it over the API, the issued key
    resolves its validated dev revision on its endpoint at that internal card; at the public
    alias it resolves nothing (no consumer listing spends a dev wallet); a consumer key
    naming the private endpoint gets the contract's not_found."""
    w = fake_world
    _, d = ready_dev(w)
    a = Api(w)
    price = {"rate_card_version": "rc_dev_1", "input_rate": "1.5", "output_rate": "3"}
    assert a.post(f"/operator/v1/deployments/{d.deployment_revision_id}/dev-rate", price,
                  key="key-rate-dev").status_code == 201
    key_id = issue(a, w, d).json()["key_id"]
    dev = auth_context(audience="provider_dev", org_id=w.A, key_id=key_id,
                       provider_org_id=w.A, endpoint_id=d.endpoint_id)
    resolved = run(catalog.resolve(w.catalog, dev, DEV_MODEL))
    assert (resolved.deployment.deployment_revision_id, resolved.rate_card.rate_card_version) \
        == (d.deployment_revision_id, "rc_dev_1")
    with pytest.raises(errors.NotFound):
        run(catalog.resolve(w.catalog, dev, ALIAS))
    consumer = auth_context(audience="consumer", org_id=IDS.consumer_org,
                            key_id=IDS.consumer_key, user_id=IDS.consumer_user)
    with pytest.raises(errors.NotFound):
        run(catalog.resolve(w.catalog, consumer, DEV_MODEL))


DEV_MODEL = "nemostation/preview-dev"      # <provider slug>/<dev endpoint name>-dev


def test_dev_keys__listing_revocation_and_wallet_are_unavailable_until_composed(fake_world):
    """Oracle: with no SR-AP06-1 reads the three calls are 503 `unavailable` (never an empty
    list or a zero balance); composed, they answer the provider's own rows and refuse a
    viewer before reading."""
    w = fake_world
    _, d = ready_dev(w)
    bare = Api(w).as_(session(w.DEV_A))
    path = K.format(d.endpoint_id)
    assert bare.get(path, provider_org_id=w.A).status_code == 503
    assert bare.get("/lab/v1/control/dev-wallet", provider_org_id=w.A).status_code == 503
    assert bare.client.delete(path + "/x", params={"provider_org_id": w.A}).status_code == 503
    a = Api(w, credentials=FakeCredentials(w.control_store))
    key_id = issue(a, w, d).json()["key_id"]
    assert [k["key_id"] for k in a.get(path, provider_org_id=w.A).json()["data"]] == [key_id]
    gone = a.client.delete(f"{path}/{key_id}", params={"provider_org_id": w.A})
    assert gone.status_code == 200 and gone.json()["revoked_at"]
    wallet = a.get("/lab/v1/control/dev-wallet", provider_org_id=w.A).json()
    assert wallet["balance"] == {"amount": "0.00000000", "unit": "CREDIT"}
    assert a.as_(session(w.VIEWER_A)).get(path, provider_org_id=w.A).status_code == 403
