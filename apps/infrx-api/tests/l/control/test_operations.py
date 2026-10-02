"""WR-LAB-API-2 / WR-R2-2 / WR-I2L-2: L3's adapters for its three consumers.

`Operations` is the `/lab/v1/control` route's `ControlOperations` port over `LabControl`;
`Serving` is R2's `ServingControl` over the same service; `app.create_app` is the I2L unit's
control-service factory. The route's records are closed (`routes/lab_control.py`); these cases
check what L3 puts in them and that the actor the route hands over is re-checked, never
trusted. The reads the adapters need (`ControlReads`) are lab-sql's to add to `PgControlStore`
(WR-LSQ-9); every case runs on the fake and, marked `pg`, on the real store.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from infrx.config import RuntimeMisconfigured
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


def test_operations__the_route_records_are_l3s_own_rows(world):
    """Oracle: register -> smoke -> propose -> an operator's approval reads back, through the
    route's closed records, exactly what L3 stored: a private dev revision pinned to the
    registered runtime digest and schema, unpriced until approved; smoke passed only after the
    engine ran; the proposal `proposed` until the operator lists it, then `approved`."""
    w, o = world, ops(world)
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


def test_operations__another_providers_actor_sees_and_moves_nothing(world):
    """Oracle: provider B's administrator lists none of A's rows and every move on A's ids is
    `not_found`; registering under A's model name is `not_found` too."""
    w, o = world, ops(world)
    d = run(o.register(actor(w, w.DEV_A, w.A), reg()))
    b = actor(w, w.ADMIN_B, w.B, v2.ProviderRole.administrator)
    for listing in (o.models, o.deployments, o.proposals):
        assert run(listing(b)) == []
    for move in (lambda: o.smoke(b, d.deployment_revision_id),
                 lambda: o.propose(b, "publish", d.deployment_revision_id),
                 lambda: o.register(b, reg())):
        with pytest.raises(errors.NotFound):
            run(move())


def test_operations__the_actor_is_rechecked_against_the_current_membership(world):
    """Oracle: the route's `Actor` is a claim; L3 asks L2 again. A viewer claiming the
    administrator role is `forbidden`; a foreign user claiming A's workspace is `not_found`."""
    w, o = world, ops(world)
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


def test_operations__a_failed_smoke_reads_failed_and_is_never_proposed(world):
    """Oracle: a failed engine smoke is `failed`/`retired` (never `passed`), and its proposal
    is a conflict; an unrun revision reads `none`, never `passed`."""
    w, o = world, ops(world)
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


def test_operations__only_a_pinned_supported_registration_is_accepted(world):
    """Oracle: the runtime is a supported image named by repository (the digest pins it), the
    schema one the gateway serves, the model one of the provider's own with pinned weights;
    a provider rollback proposal is refused (rollback is the operator's CAS)."""
    w, o = world, ops(world)
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
    weights = serving(w, "b-1", provider=w.B, model=w.MODELS[w.B], public_model_id="other/model",
                      model_version_id=w.new_id("b-model-version"))   # B's own (0007's owner key)
    assert run(w.control.register(w.DEV_B, w.B, weights))
    with pytest.raises(errors.InvalidRequest):   # weights, but no deployed limits to copy
        run(o.register(dev_b, reg(name="model")))
    with pytest.raises(errors.InvalidRequest):
        run(o.propose(actor(w, w.ADMIN_A, w.A, v2.ProviderRole.administrator), "rollback",
                      w.control_store.listings[ALIAS][0].deployment_revision_id))
    assert len(w.control_store.servings) == 2, "a refused registration wrote a row"


def test_operations__the_lab_apps_registration_shape_registers(world):
    """Oracle (0-L3I-R1): what the Lab App's form can send is what L3 accepts. The App's own
    test registration (`apps/lab/tests/l/ui/actions.test.ts` REG) is refused as invalid (an
    unsupported runtime, weights A never imported) with no row; the same shape naming A's model
    by its bare name, its imported weights and a supported runtime by digest registers, pinned
    to exactly that runtime and those weights. The slug-qualified name is not the App's."""
    w, o = world, ops(world)
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


def proposed(w, o):
    """A's registered, smoked dev revision and its administrator's open proposal."""
    dev_a = actor(w, w.DEV_A, w.A)
    d = run(o.register(dev_a, reg()))
    run(o.smoke(dev_a, d.deployment_revision_id))
    return d, run(o.propose(actor(w, w.ADMIN_A, w.A, v2.ProviderRole.administrator),
                            "publish", d.deployment_revision_id))


def test_operations__an_operator_rejects_a_proposal_and_it_publishes_nothing(world):
    """Oracle (E3L-F4): only a platform operator (`profiles.is_operator`) rejects - never a
    provider administrator or a consumer; the rejection takes the proposal out of
    `proposed_public` (the provider reads `rejected` with its instant, the revision a
    terminal prod/private/retired row), is audited under the operator with its reason, and
    publishes nothing; it is not approved afterwards nor rejected twice; an unknown id or a
    dev revision is no proposal (`not_found`); a blank reason is refused and moves nothing;
    the dev revision stays dev and may be proposed afresh."""
    w, o = world, ops(world)
    seed = pin(w, ALIAS).deployment_revision_id
    d, p = proposed(w, o)
    admin_a = actor(w, w.ADMIN_A, w.A, v2.ProviderRole.administrator)
    for user in (w.ADMIN_A, w.DEV_A, w.CONSUMER_ONLY):
        with pytest.raises(errors.Forbidden):
            run(o.operator(user))
    operator = run(o.operator(w.OPS_USER))
    assert operator.principal == f"operator:{w.OPS_USER}"
    with pytest.raises(errors.InvalidRequest):
        run(o.reject(operator, p.proposal_id, " "))
    assert [x.state for x in run(o.proposals(admin_a))] == ["proposed"], "a blank reason moved it"
    rejected = run(o.reject(operator, p.proposal_id, "not ready"))
    assert (rejected.proposal_id, rejected.kind, rejected.deployment_revision_id,
            rejected.state) == (p.proposal_id, "publish", d.deployment_revision_id, "rejected")
    assert rejected.decided_at is not None and rejected.proposed_at == p.proposed_at
    assert run(o.proposals(admin_a)) == [rejected]
    assert pin(w, ALIAS).deployment_revision_id == seed, "a rejection published something"
    rows = {x.deployment_revision_id: x for x in run(o.deployments(admin_a))}
    assert (rows[p.proposal_id].environment, rows[p.proposal_id].visibility,
            rows[p.proposal_id].state) == ("prod", "private", "retired")
    assert (rows[d.deployment_revision_id].environment,
            rows[d.deployment_revision_id].state) == ("dev", "active")
    assert run(w.control_store.deployment(p.proposal_id)).visibility is v2.Visibility.private
    event = run(w.control.events(w.ADMIN_A, w.A))[-1]
    assert (event.action, event.actor, event.subject, event.before, event.after) == (
        "lab_transition", operator.principal, p.proposal_id, {"state": "proposed_public"},
        {"state": "retired", "reason": "not ready"})
    with pytest.raises(errors.Conflict):
        run(o.reject(operator, p.proposal_id, "again"))
    with pytest.raises(errors.Conflict):
        run(w.control.approve(OPERATOR, p.proposal_id, rate_card_version="rc_rejected",
                              input_rate="3", output_rate="9", expected_version=1,
                              reason="late"))
    for other in ("a5000000-0000-4000-8000-00000000ffff", d.deployment_revision_id,
                  w.control_store.listings[ALIAS][0].deployment_revision_id):
        with pytest.raises(errors.NotFound):
            run(o.reject(operator, other, "no proposal"))
    assert pin(w, ALIAS).deployment_revision_id == seed
    again = run(o.propose(admin_a, "publish", d.deployment_revision_id))
    assert again.proposal_id != p.proposal_id and again.state == "proposed"


def test_operations__a_retired_proposal_lists_as_a_terminal_row(world):
    """Oracle (E3L-F4's repro): a proposal the platform retires outside L3 (0007's service_role
    UPDATE; its row stays `public`, which no `DeploymentRevision` may hold) leaves the
    provider's proposals and deployments listings readable - `rejected` with no operator
    instant, and a prod/private/retired row - never a 503."""
    w, o = world, ops(world)
    d, p = proposed(w, o)
    w.retire(p.proposal_id)
    viewer = actor(w, w.VIEWER_A, w.A, v2.ProviderRole.viewer)

    def answered(read):
        try:
            return run(read(viewer))
        except ValidationError:                   # what the route renders as a 503
            return "unavailable"
    proposals, deployments = answered(o.proposals), answered(o.deployments)
    assert "unavailable" not in (proposals, deployments), (proposals, deployments)
    assert [(x.proposal_id, x.state, x.decided_at) for x in proposals] == [
        (p.proposal_id, "rejected", None)]
    row = next(x for x in deployments if x.deployment_revision_id == p.proposal_id)
    assert (row.environment, row.visibility, row.state) == ("prod", "private", "retired")
    stored = run(w.control_store.deployment(p.proposal_id))
    assert (stored.visibility, stored.state) == (v2.Visibility.private, v2.DeploymentState.retired)


def test_operations__a_revision_reads_public_only_while_it_is_the_listing(world):
    """Oracle (E3L-F5, R207): the route record's visibility is the listing's truth. A pending
    proposal's prod revision reads private until the operator's approval lists it (then
    public); after the operator's rollback the rolled-back revision reads private (still
    `active`: admitted jobs keep their pins) and the restored one public. At each step the one
    public prod revision is the one App discovery pins."""
    w, o = world, ops(world)
    seed = pin(w, ALIAS).deployment_revision_id
    _, p = proposed(w, o)
    viewer = actor(w, w.VIEWER_A, w.A, v2.ProviderRole.viewer)

    def prod():
        return {x.deployment_revision_id: (x.visibility, x.state)
                for x in run(o.deployments(viewer)) if x.deployment_revision_id in (seed, p.proposal_id)}
    assert prod() == {seed: ("public", "active"), p.proposal_id: ("private", "active")}
    run(w.control.approve(OPERATOR, p.proposal_id, rate_card_version="rc_f5", input_rate="3",
                          output_rate="9", expected_version=1, reason="launch"))
    assert prod() == {seed: ("private", "active"), p.proposal_id: ("public", "active")}
    assert pin(w, ALIAS).deployment_revision_id == p.proposal_id
    run(w.control.rollback(OPERATOR, ALIAS, to_version=1, expected_version=2, reason="regress"))
    assert prod() == {seed: ("public", "active"), p.proposal_id: ("private", "active")}
    assert pin(w, ALIAS).deployment_revision_id == seed


class Sessions:
    """The session verifier (GoTrue's stand-in): `eyJ0.<user id hex>.c2ln` is that user."""

    async def user_id(self, token: str) -> str:
        return str(uuid.UUID(token.split(".")[1]))


def route(w) -> TestClient:
    """`/lab/v1/control` as R186's factory mounts it, over this world's `Operations`."""
    from fastapi import FastAPI

    from infrx.gateway.routes import lab_control as lc
    from tests.g import support
    app = FastAPI()
    lc.register(app, support.runtime(), lc.LabControl(Sessions(), w.access, ops(w)))
    return TestClient(app)


def test_control_route__only_an_operator_rejects_a_proposal(world):
    """Oracle (E3L-F4, R175): `POST /lab/v1/control/proposals/{id}/reject` is the operator's
    door on the session: no session is 401; a provider administrator and a consumer-only user
    are 403 `denied` before the body is read; the operator's body without a reason is 422,
    an unknown id 404; the rejection answers the `rejected` proposal; a second one is 409."""
    w, o = world, ops(world)
    _, p = proposed(w, o)
    client = route(w)

    def post(user, proposal=p.proposal_id, body=b'{"reason": "not ready"}'):
        head = {"content-type": "application/json"}
        if user:
            head["authorization"] = f"Bearer eyJ0.{user.replace('-', '')}.c2ln"
        return client.post(f"/lab/v1/control/proposals/{proposal}/reject", content=body,
                           headers=head)

    def answer(r):
        return r.status_code, r.json().get("refusal")
    assert answer(post(None)) == (401, "unauthenticated")
    for user in (w.ADMIN_A, w.CONSUMER_ONLY):
        assert answer(post(user, body=b"{")) == (403, "denied"), user
    assert answer(post(w.OPS_USER, body=b"{}")) == (422, "invalid")
    assert answer(post(w.OPS_USER, "a5000000-0000-4000-8000-00000000ffff")) == (404, "not_found")
    done = post(w.OPS_USER)
    assert done.status_code == 200, done.text
    assert (done.json()["proposal_id"], done.json()["state"]) == (p.proposal_id, "rejected")
    assert done.json()["decided_at"] is not None
    assert answer(post(w.OPS_USER)) == (409, "conflict")


def test_serving_control__rollback_is_a_fenced_alias_cas_that_keeps_pins(world):
    """Oracle (WR-R2-2): `serving` is the alias's current ref and listing version; a rollback
    at a stale fence is a conflict and moves nothing; at the current fence it adds a version
    naming the earlier revision, audited under the controller; a job pinned before it keeps
    its pins; an unknown or later ref is `not_found`."""
    w = world
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


@pytest.fixture
def lab_env(monkeypatch):
    """The unit's `INFRX_LAB_*` settings (never dialled here: `_store` is the world's), and
    no trace backend, so `lab_traces` is not mounted."""
    for name in control_app.REQUIRED:
        monkeypatch.setenv(name, {control_app.DATABASE_URL: "postgresql://lab@127.0.0.1:1/lab",
                                  control_app.SUPABASE_URL: "http://127.0.0.1:1"}.get(name, "anon"))
    for name in ("CLICKHOUSE_URL", "S3_TRACE_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_control_app__serves_readiness_and_no_consumer_route(world, lab_env):
    """Oracle (WR-I2L-2): the control factory answers `/readyz` 200 only while its database
    answers `infrx.now()` (503 otherwise, the reason not echoed), and mounts no consumer
    route and no docs: it is a separate, Lab-only process."""
    w, monkeypatch = world, lab_env
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


def test_control_app__mounts_only_the_lab_routers_on_its_own_settings(world, lab_env):
    """Oracle (WR-I2L-2b): the factory mounts `/lab/v1/control` over L3 (a consumer `sk-` key
    is refused there as 401 `unauthenticated`, never tried as a session), no consumer route,
    no traces without a trace backend, `/readyz` over the store's `db_now`; and it refuses to
    start, naming the setting, when any `INFRX_LAB_*` setting is missing."""
    w, monkeypatch = world, lab_env
    monkeypatch.setattr(control_app, "_store", lambda: w.control.store)
    client = TestClient(control_app.create_app())
    assert client.get("/readyz").json() == {"status": "ready"}
    key = client.get("/lab/v1/control/models", params={"provider_org_id": w.A},
                     headers={"authorization": "Bearer sk-infrx-" + "a" * 40})
    assert (key.status_code, key.json()) == (401, {"refusal": "unauthenticated"})
    for path in ("/v1/models", "/v1/jobs", "/lab/v1/traces"):
        assert client.get(path).status_code == 404, path
    for name in control_app.REQUIRED:
        with monkeypatch.context() as m:
            m.setenv(name, " ")
            with pytest.raises(RuntimeMisconfigured) as refused:
                control_app.create_app()
        assert refused.value.missing == (name,)


def test_control_app__mounts_the_judge_family_only_with_lab_judge_api(world, lab_env):
    """Oracle (WR-1, AP-08): with `LAB_JUDGE_API` the unit mounts `/lab/v1/judge/*` and the
    trace reviews; until AP-01's session actors are composed they answer 503, never act. Off
    (the default), neither path exists."""
    w, monkeypatch = world, lab_env
    monkeypatch.setattr(control_app, "_store", lambda: w.control.store)
    judge, reviews = "/lab/v1/judge/runs", "/lab/v1/traces/{request_id}/reviews"

    def paths(app):   # FastAPI 0.141 keeps an included router as one `_IncludedRouter` entry
        return {getattr(r, "path", "") for entry in app.routes
                for r in getattr(getattr(entry, "original_router", None), "routes", [entry])}
    monkeypatch.delenv("LAB_JUDGE_API", raising=False)
    off = control_app.create_app()
    assert {judge, reviews}.isdisjoint(paths(off))
    client = TestClient(off)
    assert client.get(judge, params={"provider_org_id": w.A}).status_code == 404
    assert client.post("/lab/v1/traces/r1/reviews", params={"provider_org_id": w.A},
                       headers={"Idempotency-Key": "k"}, json={}).status_code == 404
    monkeypatch.setenv("LAB_JUDGE_API", "1")
    on = control_app.create_app()
    assert {judge, reviews} <= paths(on)
    unavailable = TestClient(on).get(judge, params={"provider_org_id": w.A})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "dependency_unavailable"


def test_control_app__mounts_the_artifact_families_only_with_lab_artifacts(world, lab_env):
    """Oracle (WR-AP04-2): with `LAB_ARTIFACTS` the unit mounts AP-04's model-project and
    artifact families on its own login and the Lab objects; until AP-01's session actors are
    composed they answer 503, never act. On without `LAB_S3_BUCKET` the unit refuses to start
    naming it. Off (the default), no AP-04 path exists."""
    from infrx.lab.workers import __main__ as workers
    w, monkeypatch = world, lab_env
    monkeypatch.setattr(control_app, "_store", lambda: w.control.store)
    projects, params = "/lab/v1/control/model-projects", {"provider_org_id": w.A}
    monkeypatch.setattr(workers, "lab_objects", lambda mode, env: (
        workers.settings(mode, env, (workers.BUCKET,)) and "bucket"))
    monkeypatch.setenv("LAB_S3_BUCKET", "lab-bucket")
    training = (f"/lab/v1/providers/{w.A}/training-runs/"            # WR-AP10E-1 (AP-10 10e)
                "e0000001-0000-4000-8000-000000000001/export")
    monkeypatch.delenv("LAB_ARTIFACTS", raising=False)
    off = TestClient(control_app.create_app())
    assert off.get(projects, params=params).status_code == 404
    assert off.post(training, headers={"Idempotency-Key": "k" * 8}, json={}).status_code == 404
    monkeypatch.setenv("LAB_ARTIFACTS", "1")
    on = TestClient(control_app.create_app())
    for method, path in (("GET", projects), ("GET", "/lab/v1/operations/x"),
                         ("POST", "/lab/v1/artifacts/imports"), ("POST", training)):
        r = on.request(method, path, params=params, headers={"Idempotency-Key": "k" * 8},
                       json={})
        assert r.status_code in (503, 422), (path, r.status_code)
    unavailable = on.get(projects, params=params)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "dependency_unavailable"
    monkeypatch.delenv("LAB_S3_BUCKET")
    with pytest.raises(RuntimeMisconfigured) as refused:
        control_app.create_app()
    assert refused.value.missing == ("LAB_S3_BUCKET",)
