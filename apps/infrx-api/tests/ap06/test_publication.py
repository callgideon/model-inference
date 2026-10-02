"""AP-06 06b/06d (API-PUBLISH) at the HTTP door, on L3's fake world (`support.Api`).

The domain (`LabControl`) is L3's and already proven (tests/l/control); these cases are the
door's: who may decide, that the decision is the domain's compare-and-set at the expected
version, that a candidate without a current readiness receipt is never published, that every
mutation's Idempotency-Key replays or conflicts, and that admitted jobs keep their pins.
"""
from __future__ import annotations

from infrx.contracts.v2.money_units import Credit

from .support import (ALIAS, IDS, Api, FakeCredentials, approval, operator_actor, pin, proposed,
                      ready_dev, run, session)

P = "/operator/v1/publication-proposals"


def listed(w):
    return [v.version for v in run(w.control_store.listing_versions(ALIAS))]


def code(response):
    return response.json()["error"]["code"]


def test_publication__nothing_is_mounted_without_a_publication(fake_world):
    """Oracle: default OFF - no `rt.lab_publication`, no operator or dev-key route."""
    a = Api(fake_world, mount=False)
    for path in (P, f"{P}/x/approve", "/operator/v1/dev-wallet-grants",
                 "/lab/v1/control/dev-wallet", "/lab/v1/control/endpoints/x/keys"):
        assert a.client.post(path).status_code in (404, 405), path


def test_publication__a_non_operator_is_refused_before_any_decision(fake_world):
    """Oracle: a provider administrator, a session claiming operator that the store does not
    confirm, and an operator API key are each 403 and publish nothing."""
    w = fake_world
    _, source, prop = proposed(w)
    a = Api(w)
    a.readiness.ready(source)
    for actor in (session(w.ADMIN_A), session(w.ADMIN_A, operator=True),
                  session(w.OPS_USER, operator=True, audience="operator")):
        r = a.as_(actor).post(f"{P}/{prop.deployment_revision_id}/approve", approval())
        assert r.status_code == 403, r.text
    assert listed(w) == [1]


def test_publication__an_approval_publishes_at_the_expected_version_with_a_readiness_receipt(
        fake_world):
    """Oracle: with AP-05's receipt for the source revision serving this exact serving
    revision, the approval is listing version 2 at the approved card; new admissions pin it,
    the detail shows the candidate, its receipt and the replaced listing."""
    w = fake_world
    s, source, prop = proposed(w)
    a = Api(w)
    a.readiness.ready(source)
    detail = a.get(f"{P}/{prop.deployment_revision_id}").json()
    assert detail["candidate"]["serving_version_id"] == s.serving_version_id
    assert detail["readiness"]["ready"] is True
    assert detail["current_listing"]["version"] == 1
    assert detail["readiness_availability"]["state"] == "configured"
    r = a.post(f"{P}/{prop.deployment_revision_id}/approve", approval())
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.json() == {"public_model_id": ALIAS, "version": 2, "replayed": False,
                        "deployment_revision_id": prop.deployment_revision_id,
                        "rate_card_version": "rc_2026_10_01"}
    now = pin(w, ALIAS)
    assert (now.deployment_revision_id, now.rate_card_version) == (
        prop.deployment_revision_id, "rc_2026_10_01")
    listing = a.get(P, provider_org_id=w.A).json()["data"]
    assert [p["state"] for p in listing] == ["approved"]


def test_publication__a_stale_or_missing_readiness_receipt_refuses_approval(fake_world):
    """Oracle (stale smoke receipt): no receipt, a failed one, one for another serving
    revision, one whose source was retired since - each 409; no readiness composed is 503
    (and the detail says `disabled`); nothing is listed in any case."""
    w = fake_world
    _, source, prop = proposed(w)
    path = f"{P}/{prop.deployment_revision_id}/approve"
    a = Api(w)
    assert code(a.post(path, approval(), key="key-none-0")) == "state_conflict"
    a.readiness.ready(source, ready=False)
    assert a.post(path, approval(), key="key-failed-0").status_code == 409
    a.readiness.ready(source, serving_version_id=IDS_OTHER)
    assert a.post(path, approval(), key="key-other-0").status_code == 409
    a.readiness.ready(source)
    w.retire(source.deployment_revision_id)
    assert a.post(path, approval(), key="key-retired").status_code == 409
    a.readiness.down = True
    assert a.get(f"{P}/{prop.deployment_revision_id}").json()[
        "readiness_availability"]["state"] == "unavailable"
    assert a.post(path, approval(), key="key-down-0").status_code == 503
    assert listed(w) == [1]


IDS_OTHER = "a5000000-0000-4000-8000-0000000000ff"


def test_publication__readiness_is_required_when_none_is_composed(fake_world):
    """Oracle: with no readiness port (AP-05 not composed) the approval is 503 `unavailable`
    and retryable, the detail says `disabled`; nothing is published."""
    w = fake_world
    _, _, prop = proposed(w)
    a = Api(w, readiness=None)
    assert a.get(f"{P}/{prop.deployment_revision_id}").json()[
        "readiness_availability"]["state"] == "disabled"
    r = a.post(f"{P}/{prop.deployment_revision_id}/approve", approval())
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True
    assert listed(w) == [1]


def test_publication__a_stale_expected_version_is_a_conflict_and_its_refusal_replays(fake_world):
    """Oracle: expected version 5 while the alias is at 1 is the store's 409; the same key
    answers the same refusal (no second attempt), and a new key with the right version wins."""
    w = fake_world
    _, source, prop = proposed(w)
    a = Api(w)
    a.readiness.ready(source)
    path = f"{P}/{prop.deployment_revision_id}/approve"
    first = a.post(path, approval(expected_version=5), key="key-stale-1")
    assert first.status_code == 409, first.text
    assert a.post(path, approval(expected_version=5), key="key-stale-1").status_code == 409
    assert a.post(path, approval(), key="key-right-1").status_code == 200
    assert listed(w) == [1, 2]


def test_publication__an_approval_replays_under_its_key_and_conflicts_on_another_body(
        fake_world):
    """Oracle: the same key and body answer the same listing with `replayed: true`; the same
    key with another body is 409 `idempotency_conflict`; one listing version was added."""
    w = fake_world
    _, source, prop = proposed(w)
    a = Api(w)
    a.readiness.ready(source)
    path = f"{P}/{prop.deployment_revision_id}/approve"
    first = a.post(path, approval()).json()
    again = a.post(path, approval())
    assert again.status_code == 200 and again.json() == {**first, "replayed": True}
    other = a.post(path, approval(reason="another reason"))
    assert other.status_code == 409 and code(other) == "idempotency_conflict"
    assert listed(w) == [1, 2]


def test_publication__a_first_attempt_that_died_after_committing_is_reconciled_not_repeated(
        fake_world):
    """Oracle: a request that published and then died before recording its outcome leaves an
    unfinished receipt; once its lease lapses the retry finds the committed listing and
    answers it (replayed) - no second version, no 409 from its own write."""
    w = fake_world
    _, source, prop = proposed(w)
    a = Api(w)
    a.readiness.ready(source)
    body = approval()
    from infrx.state.control_ops import input_hash
    started = run(a.ops.start("publication.approve", operator_actor(w), "key-crash-1",
                              input_hash({"proposal_id": prop.deployment_revision_id, **body})))
    run(a.ops.lease(started.operation.operation_id, "api:dead", 60))
    run(w.control.approve(operator_session(w), prop.deployment_revision_id,
                          rate_card_version="rc_2026_10_01", input_rate="300",
                          output_rate="900", expected_version=1, reason="launch"))
    path = f"{P}/{prop.deployment_revision_id}/approve"
    assert a.post(path, body, key="key-crash-1").status_code == 409   # still leased
    a.clock.advance(61)
    r = a.post(path, body, key="key-crash-1")
    assert r.status_code == 200 and r.json()["replayed"] is True, r.text
    assert listed(w) == [1, 2]


def operator_session(w):
    from infrx.operations.service import OperatorSession
    return OperatorSession(ops=None, principal=f"operator:{w.OPS_USER}")


def test_publication__a_rejection_declines_an_open_proposal_once(fake_world):
    """Oracle: reject answers the proposal `rejected` and replays it under its key; an
    unknown id or the seeded (non-Lab) revision is 404; nothing is listed."""
    w = fake_world
    _, _, prop = proposed(w)
    a = Api(w)
    path = f"{P}/{prop.deployment_revision_id}/reject"
    r = a.post(path, {"reason": "not yet"})
    assert r.status_code == 200 and r.json()["state"] == "rejected", r.text
    assert a.post(path, {"reason": "not yet"}).json()["state"] == "rejected"
    assert a.post(f"{P}/{IDS.prod_deployment}/reject", {"reason": "x"}).status_code == 404
    assert a.get(f"{P}/{IDS.prod_deployment}").status_code == 404
    assert listed(w) == [1]


def test_publication__a_rollback_relists_an_earlier_version_and_admitted_jobs_keep_their_pins(
        fake_world):
    """Oracle: a job admitted on version 1 keeps its pins through an approval and a rollback
    (by the slash-carrying alias in the path); new admissions follow version 3 = version 1's
    revision; a stale expected version is 409 and changes nothing."""
    w = fake_world
    queued = pin(w, ALIAS)
    _, source, prop = proposed(w)
    a = Api(w)
    a.readiness.ready(source)
    assert a.post(f"{P}/{prop.deployment_revision_id}/approve", approval()).status_code == 200
    assert pin(w, ALIAS).deployment_revision_id == prop.deployment_revision_id
    path = f"/operator/v1/listings/{ALIAS}/rollback"
    stale = a.post(path, {"to_version": 1, "expected_version": 1, "reason": "x"},
                   key="key-roll-stale")
    assert stale.status_code == 409
    r = a.post(path, {"to_version": 1, "expected_version": 2, "reason": "regression"},
               key="key-roll-1")
    assert r.status_code == 200 and r.json()["version"] == 3, r.text
    again = a.post(path, {"to_version": 1, "expected_version": 2, "reason": "regression"},
                   key="key-roll-1")
    assert again.json() == {**r.json(), "replayed": True}
    assert pin(w, ALIAS) == queued
    assert listed(w) == [1, 2, 3]


def test_publication__a_dev_wallet_grant_is_exact_credit_once_per_key(fake_world):
    """Oracle: an allocation is 201 with the exact CREDIT amount; its key replays the same
    entry (200, replayed); another key is another entry; a non-operator is 403."""
    w = fake_world
    a = Api(w, credentials=FakeCredentials(w.control_store))
    body = {"provider_org_id": w.B, "amount": "250.5", "reason": "preview budget"}
    first = a.post("/operator/v1/dev-wallet-grants", body, key="key-fund-1")
    assert first.status_code == 201, first.text
    assert first.json()["amount"] == {"amount": "250.50000000", "unit": "CREDIT"}
    again = a.post("/operator/v1/dev-wallet-grants", body, key="key-fund-1")
    assert again.status_code == 200 and again.json() == {**first.json(), "replayed": True}
    assert a.post("/operator/v1/dev-wallet-grants", body, key="key-fund-2").json()[
        "entry_id"] != first.json()["entry_id"]
    assert run(w.wallets.provider_dev_wallet(w.B)).ledger_total == Credit("501")
    assert a.as_(session(w.ADMIN_B)).post("/operator/v1/dev-wallet-grants", body,
                                          key="key-fund-3").status_code == 403
    assert len(w.control_store.ledger) == 2


def test_publication__a_dev_rate_prices_only_a_validated_private_revision(fake_world):
    """Oracle: the internal card of a validated dev revision is 201 and replays (200); the
    public listed revision is 422 (an approval prices it), a draft revision 409."""
    w = fake_world
    _, d = ready_dev(w, "2026-10-01")
    a = Api(w)
    body = {"rate_card_version": "rc_dev_1", "input_rate": "1.5", "output_rate": "3"}
    path = f"/operator/v1/deployments/{d.deployment_revision_id}/dev-rate"
    r = a.post(path, body, key="key-rate-1")
    assert r.status_code == 201, r.text
    assert r.json()["input_rate_per_million"] == {"amount": "1.50000000", "unit": "CREDIT"}
    assert a.post(path, body, key="key-rate-1").status_code == 200
    assert a.post(f"/operator/v1/deployments/{IDS.prod_deployment}/dev-rate", body,
                  key="key-rate-2").status_code == 422
    draft = run(w.control.create_dev(w.DEV_A, w.A, serving_version_id=d.serving_version_id,
                                     endpoint_name="draft", max_input_tokens=1,
                                     max_output_tokens=1))
    assert a.post(f"/operator/v1/deployments/{draft.deployment_revision_id}/dev-rate",
                  body, key="key-rate-3").status_code == 409


def test_publication__an_invalid_body_or_missing_key_is_the_r270_422(fake_world):
    """Oracle: a float-looking rate, a missing reason or a missing Idempotency-Key is the
    R270 envelope (422, field errors named), never FastAPI's own `{detail}`."""
    w = fake_world
    _, _, prop = proposed(w)
    a = Api(w)
    path = f"{P}/{prop.deployment_revision_id}/approve"
    for body, key in ((approval() | {"input_rate": "1e3"}, "key-bad-1"),
                      ({k: v for k, v in approval().items() if k != "reason"}, "key-bad-2"),
                      (approval(), None)):
        r = a.post(path, body, key=key)
        assert r.status_code == 422 and "error" in r.json(), r.text
    assert listed(w) == [1]
