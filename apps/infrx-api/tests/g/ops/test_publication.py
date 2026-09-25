"""G6B.b: publishing the pinned Marlin records, a tenant's own reads, and the
operator's audited cancellation and reconciliation.

CREDIT-RATE, API-AUTH (unpriced/private), API-OPS against the fakes.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import timedelta

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import HoldState, Usage, UsageCertainty
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import Credit
from infrx.operations import cli, service
from infrx.operations.ports import HoldView
from infrx.state import migrations

from . import fakes
from .fakes import NOW, ORG_A, ORG_B, USER_A, USER_B

R = "marlin launch"
PROVIDER = "f1000001-0000-4000-8000-000000000001"


def run(coro):
    return asyncio.run(coro)


def release(effective_at=NOW, **rates):
    return service.marlin_release(provider_org_id=PROVIDER, created_at=v2fix.T0,
                                  effective_at=effective_at, **rates)


async def setup(w, *users):
    op = await w.ops.operator(w.operator_secret)
    keys = []
    for u in users:
        await op.grant_initial(u, idempotency_key=f"g-{u}", reason=R)
        keys.append(await op.issue_key(u, "sweep", idempotency_key=f"k-{u}", reason=R))
    return op, keys


def with_policy(w, deployment):
    """The data-access policy row is D/consent-owned and seeded, never published here."""
    w.catalog.policies[deployment.deployment_revision_id] = v2fix.BUILDERS[
        "data_access_policy.json"]()


def test_api_ops__the_pinned_marlin_release_is_published_and_quoted():
    w = fakes.world(empty_catalog=True)

    async def go():
        op, (key,) = await setup(w, USER_A)
        serving, deployment, card, model = release()
        with_policy(w, deployment)
        result = await op.publish(serving, deployment, card, model, idempotency_key="p1",
                                  reason=R)
        assert result["written"] == [True, True, True] and result["digest_source"] == "served_bytes"
        assert "P-01" in result["approved_by"] and card.rate_card_version.endswith("_provisional_p01")
        assert serving.weight_shard_digests == v2fix.SHARD_DIGESTS
        assert serving.runtime_image_digest is None                     # moving tag, W3 pins it
        pins, quoted = await (await w.ops.tenant(key.secret)).quote(model)
        assert pins.serving_version_id == serving.serving_version_id
        assert quoted == card and pins.rate_card_version == card.rate_card_version
        entry = w.audit.entries[-1]
        assert entry.after["operation"] == "publish" and entry.action == "admin_publish"
        count = len(w.audit.entries)
        again = await op.publish(serving, deployment, card, model, idempotency_key="p1", reason=R)
        assert again == result and len(w.audit.entries) == count
    run(go())


#: W3's measured serving version, under the repository root (a mutant copy links `models`).
SERVING_VERSION = pathlib.Path(__file__).resolve().parents[5] / "models" / "marlin2b" / \
    "serving-version.json"


def test_api_ops__the_published_release_pins_the_measured_image_and_engine_options():
    """E4B certification: the serving revision `marlin_release` publishes - pinned by every
    admission - names W3's digest-pinned runtime image and the digest of the engine options
    W3 launches, as `models/marlin2b/serving-version.json` records them. Read from the file,
    never a literal: a new serving version changes the file, and this case follows it. The
    generated fixture base and the operator seed carry the same pins."""
    measured = json.loads(SERVING_VERSION.read_text())
    image, options = measured["runtime_image"]["ref"], measured["engine_options_digest"]
    serving, _deployment, _card, _model = release()
    assert (serving.runtime_image_ref, serving.engine_options_digest) == (image, options)
    fixture = v2fix.load("serving_revision.json")
    assert (fixture["runtime_image_ref"], fixture["engine_options_digest"]) == (image, options)
    seed = (pathlib.Path(migrations.__file__).parent / "seed_marlin_provisional.sql").read_text()
    assert f"'{image}'" in seed and f"'{options}'" in seed


def test_api_auth__a_private_deployment_is_never_published_and_is_not_found():
    w = fakes.world()

    async def go():
        op, (key,) = await setup(w, USER_A)
        serving, deployment, card, model = release()
        private = v2.DeploymentRevision(**{**deployment.model_dump(),
                                          "environment": v2.Environment.dev,
                                          "visibility": v2.Visibility.private,
                                          "state": v2.DeploymentState.ready_private})
        with pytest.raises(errors.InvalidRequest):
            await op.publish(serving, private, card, model, idempotency_key="p", reason=R)
        draining = v2.DeploymentRevision(**{**deployment.model_dump(),
                                           "state": v2.DeploymentState.draining})
        with pytest.raises(errors.InvalidRequest):                        # public, not active
            await op.publish(serving, draining, card, model, idempotency_key="pd", reason=R)
        assert serving.serving_version_id not in w.catalog.servings
        assert deployment.deployment_revision_id not in w.catalog.deployments
        tenant = await w.ops.tenant(key.secret)
        with pytest.raises(errors.NotFound):
            await tenant.quote(v2fix.DEV_REQUESTED_MODEL)                # R70
        with pytest.raises(errors.NotFound):
            await tenant.quote("nemostation/unknown@1")
    run(go())


def test_credit_rate__an_unpriced_or_mispriced_deployment_is_unserveable():
    w = fakes.world()

    async def go():
        op, (key,) = await setup(w, USER_A)
        serving, deployment, card, model = release()
        for field, value in (("deployment_revision_id", v2fix.IDS.prod_deployment),
                             ("serving_version_id", v2fix.IDS.serving_version),
                             ("model_id", v2fix.IDS.model)):
            other = v2.RateCardSnapshot(**{**card.model_dump(), field: value})
            with pytest.raises(errors.InvalidRequest):
                await op.publish(serving, deployment, other, model, idempotency_key=f"p-{field}",
                                 reason=R)
        assert w.audit.entries[-1].after["operation"] == "key_issue"
        w.catalog.rate_cards.clear()                                      # nothing priced
        with pytest.raises(errors.InvalidRequest):                        # R69, not free
            await (await w.ops.tenant(key.secret)).quote(v2fix.REQUESTED_MODEL)
    run(go())


def test_credit_rate__a_stale_card_is_refused():
    w = fakes.world(empty_catalog=True)

    async def go():
        op, _ = await setup(w, USER_A)
        serving, deployment, card, model = release()
        await op.publish(serving, deployment, card, model, idempotency_key="p1", reason=R)
        for when in (NOW - timedelta(days=1), NOW):
            *_, stale, _ = release(effective_at=when, input_rate_per_million="1.00000000")
            stale = v2.RateCardSnapshot(**{**stale.model_dump(),
                                           "rate_card_version": f"rc_stale_{when:%s}"})
            with pytest.raises(errors.StateConflict):
                await op.publish(serving, deployment, stale, model, idempotency_key=f"s{when:%s}",
                                 reason=R)
        assert w.catalog.rate_cards[deployment.deployment_revision_id] == card
    run(go())


def test_credit_rate__an_admitted_request_keeps_its_admitted_rate():
    w = fakes.world(empty_catalog=True)

    async def go():
        op, (key,) = await setup(w, USER_A)
        serving, deployment, first, model = release()
        with_policy(w, deployment)
        await op.publish(serving, deployment, first, model, idempotency_key="p1", reason=R)
        tenant = await w.ops.tenant(key.secret)
        pins, card = await tenant.quote(model)
        admission = v2.AdmissionV2(
            request_id=v2fix.IDS.request, job_handle="job_" + "a" * 43, org_id=ORG_A,
            wallet_id=(await tenant.balance()).wallet_id, pins=pins, rate_card=card,
            maximum_hold=card.maximum_hold(30720, 2048), admitted_at=NOW)
        *_, second, _ = release(effective_at=NOW + timedelta(hours=1),
                                input_rate_per_million="800.00000000",
                                output_rate_per_million="2400.00000000")
        await op.publish(serving, deployment, second, model, idempotency_key="p2", reason=R)
        new_pins, _ = await tenant.quote(model)
        assert new_pins.rate_card_version == second.rate_card_version != pins.rate_card_version
        usage = Usage.of(20_000, 1_000, UsageCertainty.authoritative)
        settled = v2.settle(admission, usage, NOW + timedelta(hours=2))
        # 20,000 x 400/1e6 + 1,000 x 1,200/1e6 = 8 + 1.2 at the admitted card (not 19.2)
        assert settled.charged == Credit("9.20000000")
        assert settled.rate_card_version == first.rate_card_version
    run(go())


def test_api_ops__a_tenant_reads_only_its_own_usage_holds_and_jobs():
    w = fakes.world()

    async def go():
        _, (key_a, key_b) = await setup(w, USER_A, USER_B)
        row = v2fix.BUILDERS["usage_credit.json"]()
        w.accounts.usage_by_org[ORG_A] = v2.UsageHistory(
            org_id=ORG_A, entries=(v2.UsageRecordV2(**{**row.model_dump(), "org_id": ORG_A}),))
        w.accounts.holds_by_org[ORG_A] = (HoldView(v2fix.IDS.request, HoldState.held,
                                                   Credit("10.0144")),)
        h = w.jobs
        req = b.request(h, org_id=ORG_A)
        h.extra["grant"](ORG_A, "100")
        admitted = await h.port.admit(req, b.idem(req))
        a, bee = await w.ops.tenant(key_a.secret), await w.ops.tenant(key_b.secret)
        assert (await a.usage()).totals() == {"CREDIT": row.charged_amount}
        assert (await bee.usage()).totals() == {}                        # R73: no zero in no unit
        assert len(await a.holds()) == 1 and await bee.holds() == ()
        own, _ = await a.job(admitted.job_handle)
        assert own.request_id == req.request_id
        with pytest.raises(errors.NotFound):
            await bee.job(admitted.job_handle)
    run(go())


def test_api_ops__operator_cancellation_is_tenant_scoped_and_audited():
    w = fakes.world()

    async def go():
        op, _ = await setup(w, USER_A)
        h = w.jobs
        req = b.request(h, org_id=ORG_A)
        h.extra["grant"](ORG_A, "100")
        admitted = await h.port.admit(req, b.idem(req))
        entries = len(w.audit.entries)
        with pytest.raises(errors.NotFound):
            await op.cancel_job(ORG_B, admitted.job_handle, idempotency_key="c0", reason=R)
        assert len(w.audit.entries) == entries
        result = await op.cancel_job(ORG_A, admitted.job_handle, idempotency_key="c1", reason=R)
        assert result["state"] == "cancelled" and result["cause"] == "client_cancelled"
        entry = w.audit.entries[-1]
        assert entry.action == "admin_job_cancel" and entry.target_org_id == ORG_A
        assert entry.after["operation"] == "job_cancel"
        assert await op.cancel_job(ORG_A, admitted.job_handle, idempotency_key="c1",
                                   reason=R) == result
    run(go())


def test_api_ops__reconciliation_waits_for_its_interval_and_is_audited():
    w = fakes.world()

    async def go():
        op, _ = await setup(w, USER_A)
        request_id = v2fix.IDS.request
        w.ledger.unknown[request_id] = [ORG_A, NOW + timedelta(hours=24), "held_unknown"]
        with pytest.raises(errors.StateConflict):
            await op.reconcile(ORG_A, request_id, idempotency_key="r0", reason=R)
        with pytest.raises(errors.NotFound):
            await op.reconcile(ORG_B, request_id, idempotency_key="r1", reason=R)
        fakes.later(w.clock, hours=24)
        result = await op.reconcile(ORG_A, request_id, idempotency_key="r2", reason=R)
        assert result["settlement"] == "released_platform_absorbed"
        assert w.audit.entries[-1].action == "admin_reconcile"
        assert w.audit.entries[-1].after["operation"] == "reconcile"
    run(go())


def test_api_ops__the_cli_publishes_the_provisional_marlin_release(capsys):
    w = fakes.world(empty_catalog=True)
    env = {cli.OPERATOR_KEY_ENV: w.operator_secret}
    argv = ["publish-marlin", "--provider-org", PROVIDER, "--created-at", "2026-09-01T00:00:00+00:00",
            "--effective-at", "2026-09-22T12:00:00+00:00", "--idempotency-key", "p",
            "--reason", R]
    assert cli.main(argv, ops=w.ops, environ=env) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rate_card_version"] == "rc_marlin2b_20260922T120000Z_provisional_p01"
    assert out["requested_model"] == v2fix.REQUESTED_MODEL
    with pytest.raises(SystemExit, match="UTC offset"):
        cli.main([*argv[:6], "2026-09-23T12:00:00", *argv[7:8], "p-naive", *argv[9:]],
                 ops=w.ops, environ=env)
