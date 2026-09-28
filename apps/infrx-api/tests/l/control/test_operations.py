"""WR-LAB-API-2 / WR-R2-2 / WR-I2L-2: L3's adapters for its three consumers.

`Operations` is the `/lab/v1/control` route's `ControlOperations` port over `LabControl`;
`Serving` is R2's `ServingControl` over the same service; `app.create_app` is the I2L unit's
control-service factory. The route's records are closed (`routes/lab_control.py`); these cases
check what L3 puts in them and that the actor the route hands over is re-checked, never
trusted. The reads the adapters need (`ControlReads`) are lab-sql's to add to `PgControlStore`
(WR-LSQ-9), so the adapter cases run on the fake world; the factory's readiness runs on both.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.lab.control import app as control_app
from infrx.lab.control.operations import Actor, Operations, Registration, Serving, serving_ref

from .test_control import ship
from .worlds import ALIAS, OPERATOR, pin, serving

from infrx.contracts.v2.fixtures import SHARD_DIGESTS

# The Lab App's registration form (`apps/lab/app/(provider)/models/page.tsx`): `name` is the
# model's bare name in the workspace (`[a-z0-9][a-z0-9-]{0,62}`, no slug), `artifact_digest` the
# model's weights (one of its imported shard digests), `runtime` the image by digest.
NAME, WEIGHTS = ALIAS.rpartition("/")[2], SHARD_DIGESTS[0]
RUNTIME = "vllm/vllm-openai@sha256:" + "ab" * 32


def run(coro):
    return asyncio.run(coro)


def ops(w) -> Operations:
    return Operations(w.control, w.control_store)


def actor(w, user, provider, role=v2.ProviderRole.developer) -> Actor:
    return Actor(provider_org_id=provider, user_id=user, role=role)


def reg(**update) -> Registration:
    return Registration(**{"name": NAME, "artifact_digest": WEIGHTS, "schema_version": "chat.v1",
                           "runtime": RUNTIME, **update})


def test_operations__the_route_records_are_l3s_own_rows(fake_world):
    """Oracle: register -> smoke -> propose -> an operator's approval reads back, through the
    route's closed records, exactly what L3 stored: a private dev revision pinned to the
    registered runtime digest and schema, unpriced until approved; smoke passed only after the
    engine ran; the proposal `proposed` until the operator lists it, then `approved`."""
    w, o = fake_world, ops(fake_world)
    dev_a, admin_a = actor(w, w.DEV_A, w.A), actor(w, w.ADMIN_A, w.A, v2.ProviderRole.administrator)
    d = run(o.register(dev_a, reg()))
    assert (d.model_id, d.runtime, d.schema_version, d.environment, d.visibility, d.state,
            d.smoke, d.rate_card_version) == (ALIAS, RUNTIME, "chat.v1", "dev", "private",
                                              "active", "none", None)
    stored = w.control_store.servings[d.serving_version_id]
    assert (stored.runtime_image_ref, stored.runtime_image_digest, stored.provider_org_id) == (
        RUNTIME, RUNTIME.partition("@")[2], w.A)
    assert stored.weight_shard_digests == SHARD_DIGESTS, "a Lab registration re-pins no weights"
    prod = w.control_store.deployments[w.control_store.listings[ALIAS][0].deployment_revision_id]
    row = w.control_store.deployments[d.deployment_revision_id]
    assert (row.max_input_tokens, row.max_output_tokens) == (prod.max_input_tokens,
                                                             prod.max_output_tokens)
    assert w.control_store.endpoints[row.endpoint_id] == (w.A, "marlin-2b", v2.Environment.dev)
    model = next(m for m in run(o.models(dev_a)) if m.revision_label == d.revision_label)
    assert (model.model_id, model.artifact_digest, model.runtime) == (ALIAS, WEIGHTS, RUNTIME)
    assert w.engine.calls == []
    tested = run(o.smoke(dev_a, d.deployment_revision_id))
    assert tested.smoke == "passed" and w.engine.calls == [d.deployment_revision_id]
    p = run(o.propose(admin_a, "publish", d.deployment_revision_id))
    assert (p.kind, p.deployment_revision_id, p.state, p.decided_at) == (
        "publish", d.deployment_revision_id, "proposed", None)
    assert [(x.proposal_id, x.deployment_revision_id, x.state, x.decided_at)
            for x in run(o.proposals(dev_a))] == [(p.proposal_id, d.deployment_revision_id,
                                                   "proposed", None)]
    run(w.control.approve(OPERATOR, p.proposal_id, rate_card_version="rc_ops", input_rate="3",
                          output_rate="9", expected_version=1, reason="launch"))
    (decided,) = run(o.proposals(dev_a))
    assert decided.state == "approved" and decided.decided_at is not None
    listed = next(x for x in run(o.deployments(dev_a))
                  if x.deployment_revision_id == p.proposal_id)
    assert (listed.environment, listed.visibility, listed.rate_card_version) == (
        "prod", "public", "rc_ops")
    assert w.control_store.deployments[p.proposal_id].endpoint_id == prod.endpoint_id


def test_operations__another_providers_actor_sees_and_moves_nothing(fake_world):
    """Oracle: provider B's administrator lists none of A's rows and every move on A's ids is
    `not_found`; registering under A's model name is `not_found` too."""
    w, o = fake_world, ops(fake_world)
    d = run(o.register(actor(w, w.DEV_A, w.A), reg()))
    b = actor(w, w.ADMIN_B, w.B, v2.ProviderRole.administrator)
    for listing in (o.models, o.deployments, o.proposals):
        assert run(listing(b)) == []
    for move in (lambda: o.smoke(b, d.deployment_revision_id),
                 lambda: o.propose(b, "publish", d.deployment_revision_id),
                 lambda: o.register(b, reg())):
        with pytest.raises(errors.NotFound):
            run(move())


def test_operations__the_actor_is_rechecked_against_the_current_membership(fake_world):
    """Oracle: the route's `Actor` is a claim; L3 asks L2 again. A viewer claiming the
    administrator role is `forbidden`; a foreign user claiming A's workspace is `not_found`."""
    w, o = fake_world, ops(fake_world)
    admin = v2.ProviderRole.administrator
    with pytest.raises(errors.Forbidden):
        run(o.register(actor(w, w.VIEWER_A, w.A, admin), reg()))
    for listing in (o.models, o.deployments, o.proposals):
        with pytest.raises(errors.NotFound):
            run(listing(actor(w, w.DEV_B, w.A, admin)))
    with pytest.raises(errors.Forbidden):      # the role is refused before the name is read
        run(o.register(actor(w, w.VIEWER_A, w.A, admin), reg(name="unknown")))
    d = run(o.register(actor(w, w.DEV_A, w.A), reg()))
    run(o.smoke(actor(w, w.DEV_A, w.A), d.deployment_revision_id))
    with pytest.raises(errors.Forbidden):
        run(o.propose(actor(w, w.DEV_A, w.A, admin), "publish", d.deployment_revision_id))


def test_operations__a_failed_smoke_reads_failed_and_is_never_proposed(fake_world):
    """Oracle: a failed engine smoke is `failed`/`retired` (never `passed`), and its proposal
    is a conflict; an unrun revision reads `none`, never `passed`."""
    w, o = fake_world, ops(fake_world)
    dev_a = actor(w, w.DEV_A, w.A)
    admin_a = actor(w, w.ADMIN_A, w.A, v2.ProviderRole.administrator)
    w.engine.passes = False
    d = run(o.register(dev_a, reg()))
    failed = run(o.smoke(dev_a, d.deployment_revision_id))
    assert (failed.smoke, failed.state) == ("failed", "retired")
    listed = next(x for x in run(o.deployments(dev_a))
                  if x.deployment_revision_id == d.deployment_revision_id)
    assert (listed.smoke, listed.state) == ("failed", "retired")
    with pytest.raises(errors.Conflict):
        run(o.propose(admin_a, "publish", d.deployment_revision_id))
    fresh = run(o.register(dev_a, reg(runtime="vllm/vllm-openai@sha256:" + "cd" * 32)))
    assert fresh.smoke == "none"
    with pytest.raises(errors.Conflict):
        run(o.propose(admin_a, "publish", fresh.deployment_revision_id))


def test_operations__only_a_pinned_supported_registration_is_accepted(fake_world):
    """Oracle: the runtime is a supported image named by repository (the digest pins it), the
    schema one the gateway serves, the model one of the provider's own with pinned weights;
    a provider rollback proposal is refused (rollback is the operator's CAS)."""
    w, o = fake_world, ops(fake_world)
    dev_a = actor(w, w.DEV_A, w.A)
    for bad in (reg(runtime="vllm/vllm-openai:latest"), reg(runtime="vllm/vllm-openai"),
                reg(runtime="acme/uploaded@sha256:" + "ab" * 32), reg(schema_version="chat.v9"),
                reg(artifact_digest="sha256:" + "ee" * 32)):   # weights it was never imported with
        with pytest.raises(errors.InvalidRequest):
            run(o.register(dev_a, bad))
    with pytest.raises(errors.NotFound):
        run(o.register(dev_a, reg(name="unknown")))
    dev_b = actor(w, w.DEV_B, w.B)
    with pytest.raises(errors.NotFound):     # B's model has no operator-registered weights
        run(o.register(dev_b, reg(name="model")))
    weights = serving(w, "b-1", provider=w.B, model=w.MODELS[w.B], public_model_id="other/model")
    assert run(w.control.register(w.DEV_B, w.B, weights))
    with pytest.raises(errors.InvalidRequest):   # weights, but no deployed limits to copy
        run(o.register(dev_b, reg(name="model")))
    with pytest.raises(errors.InvalidRequest):
        run(o.propose(actor(w, w.ADMIN_A, w.A, v2.ProviderRole.administrator), "rollback",
                      w.control_store.listings[ALIAS][0].deployment_revision_id))
    assert len(w.control_store.servings) == 2, "a refused registration wrote a row"


def test_operations__the_lab_apps_registration_shape_registers(fake_world):
    """Oracle (0-L3I-R1): what the Lab App's form can send is what L3 accepts. The App's own
    test registration (`apps/lab/tests/l/ui/actions.test.ts` REG) is refused as invalid (an
    unsupported runtime, weights A never imported) with no row; the same shape naming A's model
    by its bare name, its imported weights and a supported runtime by digest registers, pinned
    to exactly that runtime and those weights. The slug-qualified name is not the App's."""
    w, o = fake_world, ops(fake_world)
    dev_a = actor(w, w.DEV_A, w.A)
    app_reg = {"name": "acme-7b", "artifact_digest": "sha256:" + "a" * 64,
               "schema_version": "chat.v2", "runtime": "vllm@sha256:bb"}
    with pytest.raises(errors.NotFound):
        run(o.register(dev_a, Registration(**app_reg)))
    with pytest.raises(errors.InvalidRequest):
        run(o.register(dev_a, Registration(**{**app_reg, "name": NAME})))
    with pytest.raises(errors.NotFound):
        run(o.register(dev_a, reg(name=ALIAS)))
    assert len(w.control_store.servings) == 1, "a refused registration wrote a row"
    d = run(o.register(dev_a, Registration(**{**app_reg, "name": NAME, "artifact_digest": WEIGHTS,
                                              "schema_version": "chat.v1", "runtime": RUNTIME})))
    stored = w.control_store.servings[d.serving_version_id]
    assert (d.model_id, d.runtime, stored.runtime_image_ref, stored.weight_shard_digests) == (
        ALIAS, RUNTIME, RUNTIME, SHARD_DIGESTS)


def test_serving_control__rollback_is_a_fenced_alias_cas_that_keeps_pins(fake_world):
    """Oracle (WR-R2-2): `serving` is the alias's current ref and listing version; a rollback
    at a stale fence is a conflict and moves nothing; at the current fence it adds a version
    naming the earlier revision, audited under the controller; a job pinned before it keeps
    its pins; an unknown or later ref is `not_found`."""
    w = fake_world
    s = Serving(w.control, w.control_store, OPERATOR)
    first = w.control_store.listings[ALIAS][0]
    endpoint = w.control_store.deployments[first.deployment_revision_id].endpoint_id
    baseline = serving_ref(w.control_store.deployments[first.deployment_revision_id],
                           w.control_store.servings[
                               w.control_store.deployments[first.deployment_revision_id]
                               .serving_version_id])
    assert run(s.serving(endpoint)) == (baseline, 1)
    _, proposal, _ = ship(w, "2026-10-01", 1)
    candidate, fence = run(s.serving(proposal.endpoint_id))
    assert fence == 2 and candidate != baseline and proposal.endpoint_id == endpoint
    queued = pin(w, ALIAS)
    with pytest.raises(errors.StateConflict):
        run(s.rollback(endpoint, fence=1, to_serving_ref=baseline, reason="stale"))
    assert run(s.serving(endpoint)) == (candidate, 2)
    with pytest.raises(errors.NotFound):
        run(s.rollback(endpoint, fence=2, to_serving_ref=candidate, reason="not earlier"))
    with pytest.raises(errors.NotFound):
        run(s.rollback(endpoint, fence=2, to_serving_ref=baseline[:-1] + (
            "1" if baseline.endswith("0") else "0"), reason="another serving's digest"))
    assert run(s.rollback(endpoint, fence=2, to_serving_ref=baseline, reason="r2")) == 3
    assert run(s.serving(endpoint)) == (baseline, 3)
    assert pin(w, ALIAS).deployment_revision_id == first.deployment_revision_id
    assert queued.deployment_revision_id == proposal.deployment_revision_id
    event = w.control_store.audit[-1]
    assert (event.action, event.actor) == ("lab_rollback", OPERATOR.principal)
    with pytest.raises(errors.NotFound):
        run(s.serving("a5000000-0000-4000-8000-00000000ffff"))


def test_control_app__serves_readiness_and_no_consumer_route(world, monkeypatch):
    """Oracle (WR-I2L-2): the control factory answers `/readyz` 200 only while its database
    answers `infrx.now()` (503 otherwise, the reason not echoed), and mounts no consumer
    route and no docs: it is a separate, Lab-only process."""
    w = world
    monkeypatch.setattr(control_app, "_store", lambda: w.control.store)
    client = TestClient(control_app.create_app())
    assert client.get("/readyz").status_code == 200
    for path in ("/v1/models", "/v1/chat/completions", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404, path

    class Down:
        async def db_now(self):
            raise OSError("connection refused: secret-dsn")
    monkeypatch.setattr(control_app, "_store", lambda: Down())
    down = TestClient(control_app.create_app()).get("/readyz")
    assert down.status_code == 503 and "secret" not in down.text
