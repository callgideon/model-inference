"""L3 LAB-PUBLISH / LAB-ACCESS at the Lab's control entry point (`infrx.lab.control.LabControl`).

Every case taking `world` runs on the fake of lab-sql's L3-SQL seam and, marked `pg`, on
`PgControlStore` beside A3's PostgreSQL registry and catalog (it skips visibly until L3-SQL
merges). The provider's identity is the server session's `user_id`; an operator's is the
`OperatorSession` that `Operations.operator(secret)` builds.
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.auth.context import auth_context
from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import Credit
from infrx.operations.service import hash_key

from .worlds import ALIAS, IDS, OPERATOR, pin, serving

DRAFT, READY = v2.DeploymentState.draft, v2.DeploymentState.ready_private


def run(coro):
    return asyncio.run(coro)


def dev(w, user, provider, serving_version_id, endpoint="preview"):
    return run(w.control.create_dev(user, provider, serving_version_id=serving_version_id,
                                    endpoint_name=endpoint, max_input_tokens=30720,
                                    max_output_tokens=2048))


def ready_dev(w, label="2026-10-01", endpoint="preview"):
    """A's new serving revision registered and validated on a private dev endpoint."""
    s = serving(w, label)
    assert run(w.control.register(w.DEV_A, w.A, s)) is True
    d = dev(w, w.DEV_A, w.A, s.serving_version_id, endpoint)
    return s, run(w.control.validate(w.DEV_A, w.A, d.deployment_revision_id))


def ship(w, label, expected_version):
    """register -> dev smoke -> proposal (administrator) -> approval (operator)."""
    s, d = ready_dev(w, label, endpoint=f"preview-{label}")
    proposal = run(w.control.propose(w.ADMIN_A, w.A, d.deployment_revision_id,
                                     endpoint_name="marlin-2b"))
    listing = run(w.control.approve(OPERATOR, proposal.deployment_revision_id,
                                    rate_card_version=f"rc_{label.replace('-', '_')}",
                                    input_rate="300", output_rate="900",
                                    expected_version=expected_version, reason="launch"))
    return s, proposal, listing


def actions(w, provider):
    return [e.action for e in run(w.control.events(w.ADMIN_A if provider == w.A
                                                   else w.ADMIN_B, provider))]


# --- the seam: cross-provider mutation -------------------------------------------
def test_lab_control__a_provider_never_mutates_another_providers_registry(world):
    """Oracle: B's administrator reaches none of A's registry: not by naming A's model or
    serving in B's workspace, not by naming A's workspace, not A's dev revision by id. Every
    refusal is `not_found` (a foreign id confirms nothing), and A's revision is untouched."""
    w = world
    s = serving(w, "2026-10-01")
    assert run(w.control.register(w.DEV_A, w.A, s))
    d = dev(w, w.DEV_A, w.A, s.serving_version_id)
    foreign = serving(w, "2026-10-02", provider=w.B)                     # A's model, B's row
    claimed = serving(w, "2026-10-04", model=w.MODELS[w.B])              # B's model, A's row
    for provider, row in ((w.B, s), (w.B, foreign), (w.B, claimed),
                          (w.A, serving(w, "2026-10-03"))):
        with pytest.raises(errors.NotFound):
            run(w.control.register(w.ADMIN_B, provider, row))
    with pytest.raises(errors.NotFound):
        dev(w, w.ADMIN_B, w.B, s.serving_version_id)
    for provider in (w.A, w.B):
        with pytest.raises(errors.NotFound):
            run(w.control.validate(w.ADMIN_B, provider, d.deployment_revision_id))
        with pytest.raises(errors.NotFound):
            run(w.control.issue_dev_key(w.ADMIN_B, provider, d.deployment_revision_id))
        with pytest.raises(errors.NotFound):
            run(w.control.propose(w.ADMIN_B, provider, d.deployment_revision_id,
                                  endpoint_name="marlin-2b"))
    with pytest.raises(errors.NotFound):
        run(w.control.events(w.ADMIN_B, w.A))
    assert run(w.control.validate(w.DEV_A, w.A, d.deployment_revision_id)).state is READY


def test_lab_control__roles_bound_every_operation(world):
    """Oracle: a viewer registers nothing and reads no control history; a developer builds
    and validates dev revisions but never proposes publication; a consumer-only user has no
    workspace at all."""
    w = world
    s = serving(w, "2026-10-01")
    with pytest.raises(errors.Forbidden):
        run(w.control.register(w.VIEWER_A, w.A, s))
    with pytest.raises(errors.NotFound):
        run(w.control.register(w.CONSUMER_ONLY, w.A, s))
    with pytest.raises(errors.Forbidden):
        run(w.control.events(w.VIEWER_A, w.A))
    _, d = ready_dev(w)
    with pytest.raises(errors.Forbidden):
        run(w.control.propose(w.DEV_A, w.A, d.deployment_revision_id, endpoint_name="marlin-2b"))
    assert run(w.control.propose(w.ADMIN_A, w.A, d.deployment_revision_id,
                                 endpoint_name="marlin-2b")).state \
        is v2.DeploymentState.proposed_public


BAD_ARTIFACTS = {
    "moving_tag": lambda s: {"runtime_image_ref": "vllm/vllm-openai:latest"},
    "unsupported_runtime": lambda s: {"runtime_image_ref": "evil/runner@sha256:" + "ab" * 32},
    "unsupported_schema": lambda s: {"capability": s.capability.model_copy(
        update={"input_schema_ref": "custom.request.v9"})},
}


@pytest.mark.parametrize("bad", sorted(BAD_ARTIFACTS))
def test_lab_control__only_a_pinned_supported_artifact_registers(world, bad):
    """Oracle: the runtime is a supported image pinned by digest (never a moving tag or an
    arbitrary uploaded image) and the request/response schemas are ones the gateway serves;
    a registration is immutable - the same row again is a no-op, another row under its id a
    conflict."""
    w = world
    s = serving(w, "2026-10-01")
    update = BAD_ARTIFACTS[bad](s)
    with pytest.raises(errors.InvalidRequest):
        run(w.control.register(w.DEV_A, w.A, s.model_copy(update=update)))
    assert run(w.control.register(w.DEV_A, w.A, s)) is True
    assert run(w.control.register(w.DEV_A, w.A, s)) is False
    with pytest.raises(errors.Conflict):
        run(w.control.register(w.DEV_A, w.A, s.model_copy(update={"precision": "fp8"})))


def test_lab_control__only_a_passed_smoke_makes_a_dev_revision_usable(world):
    """Oracle: validation runs the engine's smoke on the dev revision; a failure retires it,
    and a retired revision gets no credential and no publication proposal - a failed
    validation never becomes public. A pass makes it ready_private, audited to its actor."""
    w = world
    s = serving(w, "2026-10-01")
    run(w.control.register(w.DEV_A, w.A, s))
    w.engine.passes = False
    failed = dev(w, w.DEV_A, w.A, s.serving_version_id, "broken")
    assert failed.state is DRAFT
    assert run(w.control.validate(w.DEV_A, w.A, failed.deployment_revision_id)).state \
        is v2.DeploymentState.retired
    with pytest.raises(errors.StateConflict):
        run(w.control.issue_dev_key(w.DEV_A, w.A, failed.deployment_revision_id))
    with pytest.raises(errors.StateConflict):
        run(w.control.propose(w.ADMIN_A, w.A, failed.deployment_revision_id,
                              endpoint_name="marlin-2b"))
    with pytest.raises(errors.StateConflict):
        run(w.control.validate(w.DEV_A, w.A, failed.deployment_revision_id))
    w.engine.passes = True
    good = dev(w, w.DEV_A, w.A, s.serving_version_id, "good")
    assert run(w.control.validate(w.DEV_A, w.A, good.deployment_revision_id)).state is READY
    assert w.engine.calls == [failed.deployment_revision_id, good.deployment_revision_id]
    assert [(e.action, e.actor, e.after["state"]) for e in run(w.control.events(w.ADMIN_A, w.A))
            if e.subject == good.deployment_revision_id] == [
        ("lab_transition", w.DEV_A, "validating"), ("lab_transition", w.DEV_A, "ready_private")]


def test_lab_control__a_dev_revision_never_reaches_app_discovery(world):
    """Oracle: a validated, priced dev revision with its key is reachable only by a
    provider_dev credential for its own endpoint; a consumer resolves the public listing
    exactly as before (and nothing under the dev names); an operator cannot publish it, and
    an internal card never lands on a public deployment."""
    w = world
    before = pin(w, ALIAS)
    s, d = ready_dev(w)
    with pytest.raises(errors.InvalidRequest):              # unpriced: never a free preview
        pin(w, "nemostation/preview-dev", auth_for(w, d))
    card = run(w.control.price_dev(OPERATOR, d.deployment_revision_id,
                                   rate_card_version="rc_preview_internal", input_rate="400",
                                   output_rate="1200"))
    assert card.approved_by == OPERATOR.principal
    with pytest.raises(errors.InvalidRequest):
        run(w.control.price_dev(OPERATOR, IDS.prod_deployment, rate_card_version="rc_leak",
                                input_rate="0", output_rate="0"))
    key = run(w.control.issue_dev_key(w.DEV_A, w.A, d.deployment_revision_id))
    assert pin(w, "nemostation/preview-dev", auth_for(w, d, key.key_id)) \
        .deployment_revision_id == d.deployment_revision_id
    assert pin(w, ALIAS) == before
    for name in ("nemostation/preview-dev", f"nemostation/preview-dev@{s.revision_label}",
                 f"{ALIAS}@{s.revision_label}"):
        with pytest.raises(errors.NotFound):
            pin(w, name)
    with pytest.raises(errors.NotFound):                     # another endpoint's credential
        pin(w, "nemostation/preview-dev", auth_for(w, d, key.key_id, IDS.prod_endpoint))
    with pytest.raises(errors.NotFound):                     # a public id is no dev revision
        run(w.control.validate(w.DEV_A, w.A, IDS.prod_deployment))
    with pytest.raises(errors.StateConflict):
        run(w.control.approve(OPERATOR, d.deployment_revision_id, rate_card_version="rc_dev_pub",
                              input_rate="1", output_rate="1", expected_version=1, reason="x"))
    assert pin(w, ALIAS) == before


def auth_for(w, d, key_id=IDS.provider_dev_key, endpoint_id=None):
    return auth_context(audience="provider_dev", org_id=w.A, key_id=key_id,
                        provider_org_id=w.A, endpoint_id=endpoint_id or d.endpoint_id)


def test_lab_control__a_dev_credential_is_scoped_to_its_endpoint(world):
    """Oracle: the stored key names the provider and the dev endpoint of the revision it was
    issued for, and holds only the secret's hash; the secret is returned once."""
    w = world
    _, d = ready_dev(w)
    key = run(w.control.issue_dev_key(w.DEV_A, w.A, d.deployment_revision_id))
    assert w.key_scope(key.key_id) == (w.A, d.endpoint_id, hash_key(key.secret))
    assert key.secret.startswith(key.prefix) and key.org_id == w.A
    assert "lab_dev_key" in actions(w, w.A)


def test_lab_control__a_dev_wallet_starts_at_zero_and_only_audited_allocations_fund_it(world):
    """Oracle: provider B's provider_dev wallet opens at 0 CREDIT (no signup grant), is
    funded only by an operator allocation, exactly once per idempotency key, and each
    allocation is in B's control history under the operator's name."""
    w = world
    assert run(w.wallets.provider_dev_wallet(w.B)) is None
    entry = run(w.control.fund(OPERATOR, w.B, "250.5", idempotency_key="fund-1",
                               reason="preview budget"))
    again = run(w.control.fund(OPERATOR, w.B, "250.5", idempotency_key="fund-1",
                               reason="preview budget"))
    assert again.entry_id == entry.entry_id and entry.kind is v2.LedgerEntryKind.operator_allocation
    wallet = run(w.wallets.provider_dev_wallet(w.B))
    assert (wallet.kind, wallet.ledger_total, wallet.reserved_total) == (
        v2.WalletKind.provider_dev, Credit("250.5"), Credit("0"))
    assert not wallet.has_signup_entitlement
    with pytest.raises(errors.InvalidRequest):
        run(w.control.fund(OPERATOR, w.B, "0", idempotency_key="fund-2", reason="x"))
    assert [(e.action, e.actor) for e in run(w.control.events(w.ADMIN_B, w.B))] == [
        ("lab_fund", OPERATOR.principal)]


# --- publication, pins, CAS, rollback ----------------------------------------------
def test_lab_control__an_alias_switch_while_a_job_is_queued_keeps_its_pins(world):
    """Oracle: a job admitted (and queued) on listing 1 keeps its serving and rate pins
    through a publication and a rollback: the rows it names are unchanged and stay
    servable; new admissions follow the listing - to the new revision, then back."""
    w = world
    queued = pin(w, ALIAS)
    old_serving = run(w.catalog.serving_revision(queued.serving_version_id))
    old_card = run(w.catalog.active_rate_card(queued.deployment_revision_id))
    s, proposal, listing = ship(w, "2026-10-01", expected_version=1)
    assert listing.version == 2
    now = pin(w, ALIAS)
    assert (now.deployment_revision_id, now.serving_version_id, now.rate_card_version) == (
        proposal.deployment_revision_id, s.serving_version_id, "rc_2026_10_01")
    assert run(w.catalog.serving_revision(queued.serving_version_id)) == old_serving
    assert run(w.catalog.active_rate_card(queued.deployment_revision_id)) == old_card
    assert run(w.control.rollback(OPERATOR, ALIAS, to_version=1, expected_version=2,
                                  reason="regression")).version == 3
    assert pin(w, ALIAS) == queued
    assert pin(w, f"{ALIAS}@{s.revision_label}").rate_card_version == "rc_2026_10_01"
    assert actions(w, w.A)[-2:] == ["lab_publish", "lab_rollback"]


def test_lab_control__publication_and_rollback_are_compare_and_set(world):
    """Oracle: an approval or rollback against a listing version that is no longer current
    is a state conflict and changes nothing - two operators racing one version publish one
    revision, and the loser's proposal stays proposed."""
    w = world
    _, first, _ = ship(w, "2026-10-01", expected_version=1)
    _, d = ready_dev(w, "2026-10-02", endpoint="preview-b")
    second = run(w.control.propose(w.ADMIN_A, w.A, d.deployment_revision_id,
                                   endpoint_name="marlin-2b"))
    with pytest.raises(errors.StateConflict):
        run(w.control.approve(OPERATOR, second.deployment_revision_id, rate_card_version="rc_b",
                              input_rate="1", output_rate="1", expected_version=1, reason="x"))
    with pytest.raises(errors.StateConflict):
        run(w.control.rollback(OPERATOR, ALIAS, to_version=1, expected_version=1, reason="x"))
    assert pin(w, ALIAS).deployment_revision_id == first.deployment_revision_id
    assert run(w.control.approve(OPERATOR, second.deployment_revision_id, rate_card_version="rc_b",
                                 input_rate="1", output_rate="1", expected_version=2,
                                 reason="x")).version == 3


def test_lab_control__a_rollback_targets_an_earlier_servable_listing_only(world):
    """Oracle: a rollback names an earlier listing version whose deployment is still active;
    the current version, a later one and a retired deployment are refused."""
    w = world
    ship(w, "2026-10-01", expected_version=1)
    for version in (2, 3):
        with pytest.raises(errors.NotFound):
            run(w.control.rollback(OPERATOR, ALIAS, to_version=version, expected_version=2,
                                   reason="x"))
    w.retire(IDS.prod_deployment)
    with pytest.raises(errors.StateConflict):
        run(w.control.rollback(OPERATOR, ALIAS, to_version=1, expected_version=2, reason="x"))
