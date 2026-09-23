#!/usr/bin/env python3
"""D5 items 4 and 7: the operator money operations (0018) and the PostgreSQL adapters of
G6B's ports (`infrx/state/operations.py`) against a REAL PostgreSQL (both images).

    INFRX_D_TASK=d5 uv run --frozen pytest -q tests/d/test_operations_pg.py
    INFRX_D_TASK=d5 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_operations_pg.py
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_admission, checks_operations, pgharness

DB = f"{pgharness.DATABASE}_operations"

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
_state: dict = {}


def _db():
    if "conn" not in _state:
        pgharness.ensure()
        pgharness.recreate(DB)
        pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(DB)
        checks_admission.seed_admission(conn)
        _state["conn"] = conn
    return _state["conn"]


# --- item 4: grant_credit and reconcile ---------------------------------------------------
def test_credit_spend__adjust_is_audited_idempotent_and_never_below_reserved() -> None:
    print(checks_operations.check_adjust(_db()))


def test_credit_spend__allocation_only_to_provider_dev_wallets() -> None:
    print(checks_operations.check_allocation(_db()))


def test_dur_settle__reconcile_waits_for_the_db_clock_and_never_debits() -> None:
    print(checks_operations.check_reconcile_clock(_db()))


def test_dur_settle__reconcile_is_tenant_bound() -> None:
    print(checks_operations.check_reconcile_tenant(_db()))


# --- item 10a: the privilege surface ---------------------------------------------------------
def test_privileges__d5_operations_service_only_helpers_nobody() -> None:
    print(checks_operations.check_d5_privileges(_db()))


# --- item 7: the PostgreSQL adapters of G6B's ports ---------------------------------------
# These drive the adapters on their own connections (committed rows), so they share one
# database of their own, built once: the admission scenario, every id fresh per test.
import asyncio  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from infrx.contracts import errors  # noqa: E402
from infrx.contracts.conformance import builders as b  # noqa: E402
from infrx.contracts.records import Role, Usage  # noqa: E402
from infrx.contracts.v2 import fixtures as v2fix  # noqa: E402
from infrx.contracts.v2.money_units import Credit  # noqa: E402
from infrx.contracts.v2.records import (CredentialAudience, DeploymentRevision,  # noqa: E402
                                        RateCardSnapshot, ServingRevision)
from infrx.operations.ports import AuditEntry, KeyRow  # noqa: E402
from infrx.state import operations as ops  # noqa: E402
from infrx.state.jobstore import PgJobStore, connector  # noqa: E402

from . import checks_credit as cc  # noqa: E402
from . import checks_signup  # noqa: E402

ADAPTERS = f"{pgharness.DATABASE}_ops_adapters"
AT = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _adapters():
    if "adapters" not in _state:
        pgharness.ensure()
        pgharness.recreate(ADAPTERS)
        pgharness.apply(ADAPTERS, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        owner = pgharness.connect(ADAPTERS)
        checks_admission.seed_admission(owner)
        _state["adapters"] = (owner, connector(pgharness.dsn(ADAPTERS)))
    return _state["adapters"]


def run(coro):
    return asyncio.run(coro)


def _key(org: str, **kw) -> KeyRow:
    key_id = str(uuid.uuid4())
    return KeyRow(key_id=key_id, org_id=org, audience=CredentialAudience.consumer,
                  key_hash=kw.pop("key_hash", uuid.uuid4().hex * 2), prefix="sk-infrx-abcdefgh",
                  name="sweep", user_id=kw.pop("user_id", cc.CONSUMER_1), created_at=AT, **kw)


def test_api_ops__tenant_keys_are_tenant_scoped_one_row_and_revoked_once() -> None:
    owner, connect = _adapters()
    tenants = ops.PgTenantStore(connect)
    org = cc.personal_org(owner, cc.CONSUMER_1)
    row = _key(org)
    assert run(tenants.insert_key(row)) is True
    assert run(tenants.insert_key(row)) is False, "a replay wrote a second row"
    assert owner.execute("select count(*) from public.api_keys where id = %s",
                         (row.key_id,)).fetchone()[0] == 1
    with pytest.raises(errors.Conflict):
        run(tenants.insert_key(_key(org, key_hash=row.key_hash)))
    stored = run(tenants.key(org, row.key_id))
    assert (stored.key_id, stored.org_id, stored.key_hash, stored.audience, stored.role,
            stored.user_id) == (row.key_id, org, row.key_hash, CredentialAudience.consumer,
                                Role.service, cc.CONSUMER_1), stored
    assert run(tenants.key(b.ORG_B, row.key_id)) is None, "another org read the key"
    assert run(tenants.key_by_hash(row.key_hash)) == stored
    assert run(tenants.key_by_hash("0" * 64)) is None
    with pytest.raises(errors.NotFound):
        run(tenants.revoke_key(b.ORG_B, row.key_id, AT))
    first = run(tenants.revoke_key(org, row.key_id, AT)).revoked_at
    assert first is not None
    owner.execute("select infrx_test.advance(60)")
    assert run(tenants.revoke_key(org, row.key_id, AT)).revoked_at == first, \
        "a second revocation rewrote revoked_at"
    operator = owner.execute("select id::text from public.api_keys where audience = 'operator' "
                             "and revoked_at is null").fetchone()[0]
    assert run(tenants.key_by_hash(f"hash-{operator}")).role is Role.operator


def test_api_ops__suspension_is_one_audited_code() -> None:
    owner, connect = _adapters()
    tenants = ops.PgTenantStore(connect)
    assert run(tenants.suspension(b.ORG_B)) is None
    run(tenants.set_suspension(b.ORG_B, "abuse", AT))
    assert run(tenants.suspension(b.ORG_B)) == "abuse"
    run(tenants.set_suspension(b.ORG_B, None, AT.replace(minute=1)))
    assert run(tenants.suspension(b.ORG_B)) is None
    actions = owner.execute("select action from infrx.audit_entries where target_org_id = %s "
                            "and idempotency_key like 'set_suspension:%%'", (b.ORG_B,)).fetchall()
    assert actions == [("admin_set_suspension",), ("admin_set_suspension",)], actions


def test_api_ops__the_audit_log_appends_once_and_answers_only_its_key() -> None:
    _owner, connect = _adapters()
    audit = ops.PgAuditLog(connect)
    key = f"k-{uuid.uuid4()}"
    entry = AuditEntry(id=str(uuid.uuid4()), at=AT, actor_principal="ops@test",
                       action="admin_grant", target_org_id=b.ORG_A, reason="a reason",
                       before=None, after={"operation": "x", "result": {"n": 1}},
                       idempotency_key=key)
    assert run(audit.by_idempotency_key(key)) is None
    run(audit.append(entry))
    assert run(audit.by_idempotency_key(key)) == entry
    assert run(audit.by_idempotency_key(f"k-{uuid.uuid4()}")) is None, "another key answered"
    for clash in (entry, AuditEntry(**{**entry.__dict__, "id": str(uuid.uuid4())})):
        with pytest.raises(errors.Conflict):
            run(audit.append(clash))


def _release(label: str):
    """A new serving revision of the seeded Marlin model on the seeded prod endpoint, its
    public deployment and its card (the provider, model and endpoint rows exist)."""
    serving = ServingRevision.model_validate({
        **v2fix.BUILDERS["serving_revision.json"]().model_dump(mode="json"),
        "serving_version_id": str(uuid.uuid4()), "model_version_id": str(uuid.uuid4()),
        "revision_label": label})
    deployment = DeploymentRevision.model_validate({
        **v2fix.BUILDERS["deployment_revision_public.json"]().model_dump(mode="json"),
        "deployment_revision_id": str(uuid.uuid4()),
        "serving_version_id": serving.serving_version_id})
    card = RateCardSnapshot.model_validate({
        **v2fix.BUILDERS["rate_card_marlin.json"]().model_dump(mode="json"),
        "rate_card_version": f"rc_d5_{label}", "effective_at": "2026-09-01T00:00:00Z",
        "deployment_revision_id": deployment.deployment_revision_id,
        "serving_version_id": serving.serving_version_id})
    return serving, deployment, card


def test_api_ops__registry_rows_are_immutable_and_the_alias_moves() -> None:
    owner, connect = _adapters()
    registry = ops.PgRegistry(connect)
    serving, deployment, card = _release("d5-reg")
    for record in (serving, deployment, card):
        assert run(registry.put(record)) is True, record
        assert run(registry.put(record)) is False, f"an identical re-insert wrote: {record}"
    for changed in (serving.model_copy(update={"precision": "fp8"}),
                    deployment.model_copy(update={"max_output_tokens": 1024}),
                    card.model_copy(update={"approved_by": "someone else"})):
        with pytest.raises(errors.Conflict):
            run(registry.put(changed))
    orphan = deployment.model_copy(update={"deployment_revision_id": str(uuid.uuid4()),
                                           "endpoint_id": str(uuid.uuid4())})
    with pytest.raises(errors.InvalidRequest):
        run(registry.put(orphan))
    alias = f"{v2fix.PUBLIC_MODEL_ID}@d5-reg"
    before = owner.execute("select count(*) from infrx.catalog_listings").fetchone()[0]
    run(registry.move_alias(alias, deployment.deployment_revision_id))
    run(registry.move_alias(alias, deployment.deployment_revision_id))
    assert owner.execute("select count(*) from infrx.catalog_listings").fetchone()[0] == \
        before + 1, "an idempotent move wrote twice"
    pins = owner.execute("select deployment_revision_id::text, rate_card_version from "
                         "infrx.resolve_admission_pins(%s)", (alias,)).fetchone()
    assert pins == (deployment.deployment_revision_id, card.rate_card_version), pins


def test_api_ops__a_tenant_reads_its_own_usage_per_unit_and_its_credit_holds() -> None:
    owner, connect = _adapters()
    store = PgJobStore(connect)
    view = ops.PgAccountView(connect)
    from infrx.state.pgtesting import PgClock
    world = type("World", (), {"clock": PgClock(owner), "ids": checks_admission._Ids()})()
    org = cc.personal_org(owner, cc.CONSUMER_1)

    async def settle(request, regime):
        admit = store.admit if regime == "legacy_usd" else store.admit_credit
        await admit(request, b.idem(request, request.request_id))
        prep = await store.claim_preparation(request.request_id, "prep")
        await store.prepared(prep)
        lease = await store.claim(request.request_id, "w")
        ref = await store.put_result(request.request_id, "done")
        outcome = b.outcome(request.request_id, world, tokens=Usage.of(1200, 340), result_ref=ref)
        if regime == "legacy_usd":
            return await store.complete(lease, outcome), None
        return await store.complete_credit(lease, outcome)
    usd, _ = run(settle(b.request(world, org_id=b.ORG_A), "legacy_usd"))
    _, credit = run(settle(checks_admission.credit_request(world, checks_admission.C1_KEY, org),
                           "credit"))
    held = checks_admission.credit_request(world, checks_admission.C1_KEY, org)
    run(store.admit_credit(held, b.idem(held, held.request_id)))
    assert run(view.usage(b.ORG_A)).totals()["USD"] == f"{usd.debit:f}"
    assert run(view.usage(org)).totals() == {"CREDIT": str(credit.charged)}, \
        run(view.usage(org)).totals()
    assert run(view.usage(b.ORG_B)).totals() == {}, "R73: no zero in a unit nobody used"
    holds = run(view.holds(org))
    assert [h.request_id for h in holds] == [held.request_id], holds
    assert run(view.holds(b.ORG_A)) == (), "a USD hold was read as CREDIT"


def test_api_ops__the_ledger_port_adjusts_reconciles_and_grants_through_d5_and_a1() -> None:
    owner, connect = _adapters()
    ledger, wallets = ops.PgLedger(connect), ops.PgWalletDirectory(connect)
    wallet = run(wallets.consumer_wallet_for_user(cc.CONSUMER_2))
    assert wallet is not None and wallet.personal_org_id == cc.personal_org(owner, cc.CONSUMER_2)
    assert run(wallets.provider_dev_wallet(cc.NEMO)).wallet_id == cc.PROVIDER_WALLET
    op = str(uuid.uuid4())
    entry, replayed = run(ledger.adjust(wallet, Credit("2.50000000"), op, "ops@test",
                                        "goodwill", AT))
    assert (entry.kind.value, str(entry.amount), entry.operation_id, replayed) == \
        ("operator_adjustment", "2.50000000", op, False), entry
    again, replayed = run(ledger.adjust(wallet, Credit("2.50000000"), op, "ops@test",
                                        "goodwill", AT))
    assert (again, replayed) == (entry, True)
    assert run(wallets.consumer_wallet_for_user(cc.CONSUMER_2)).ledger_total == \
        wallet.ledger_total + Credit("2.50000000")
    with pytest.raises(errors.NotFound):
        run(ledger.reconcile(b.ORG_B, str(uuid.uuid4()), "r", "ops@test", AT))
    checks_signup.gotrue_columns(owner)     # the bare Supabase image has no GoTrue columns
    owner.execute("update auth.users set email_confirmed_at = infrx.now() where id = %s",
                  (cc.CONSUMER_2,))
    identity = run(ops.PgSignup(ops._Db(connect)).verified_user(cc.CONSUMER_2))
    grant, replayed = run(ledger.grant_initial(identity, str(uuid.uuid4()), AT))
    assert (grant.wallet_id, replayed) == (wallet.wallet_id, True), (grant, replayed)


# --- item 7: G6B's service on the PostgreSQL adapters (the E3B item-6 path) ---------------
from infrx.contracts.limits import DEFAULTS  # noqa: E402
from infrx.operations import service  # noqa: E402
from infrx.state.catalog import PgCatalogDirectory  # noqa: E402
from infrx.state.journal import PgStreamStore  # noqa: E402

SERVICE_DB = f"{pgharness.DATABASE}_ops_service"
R = "support ticket 42"


def _operations():
    """`service.Operations` composed from the PostgreSQL adapters only (the ports G6B's
    fakes stood in for), on a committed admission-scenario database with a bootstrapped
    operator key whose secret this test holds."""
    if "service" not in _state:
        pgharness.ensure()
        pgharness.recreate(SERVICE_DB)
        pgharness.apply(SERVICE_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        owner = pgharness.connect(SERVICE_DB)
        checks_admission.seed_admission(owner)
        owner.execute("update public.api_keys set revoked_at = infrx.now() "
                      "where audience = 'operator'")
        secret = service.new_secret()
        owner.execute("select infrx.bootstrap_operator_key(%s, 'bootstrap', %s, %s, "
                      "'ops@test', 'operator bootstrap')",
                      (b.ORG_B, secret[:service.PREFIX_CHARS], service.hash_key(secret)))
        checks_signup.gotrue_columns(owner)
        owner.execute("update auth.users set email_confirmed_at = infrx.now() where id = %s",
                      (cc.CONSUMER_1,))
        connect = connector(pgharness.dsn(SERVICE_DB))
        built = service.Operations(
            identities=ops.PgSignup(ops._Db(connect)), tenants=ops.PgTenantStore(connect),
            ledger=ops.PgLedger(connect), audit=ops.PgAuditLog(connect),
            registry=ops.PgRegistry(connect), wallets=ops.PgWalletDirectory(connect),
            catalog=PgCatalogDirectory(connect), jobs=PgJobStore(connect),
            accounts=ops.PgAccountView(connect),
            clock=lambda: datetime.now(timezone.utc))
        _state["service"] = (owner, connect, built, secret)
    return _state["service"]


def test_api_ops__the_operations_service_runs_on_the_postgres_adapters() -> None:
    """G6B's operator and tenant sessions end to end on PostgreSQL: the operator key's
    audience, the A1 grant (replayed), a key revealed once, a tenant's own balance and
    quote, an audited idempotent adjustment, a suspension that refuses new keys, a
    revocation that ends the key, a tenant-scoped cancellation, and the 24 h
    reconciliation - every write audited once under the operator's idempotency key."""
    owner, connect, ops_, secret = _operations()
    org = cc.personal_org(owner, cc.CONSUMER_1)

    async def go():
        op = await ops_.operator(secret)
        granted = await op.grant_initial(cc.CONSUMER_1, idempotency_key="g1", reason=R)
        assert granted["replayed"] is True and granted["amount"] == "10000.00000000", granted
        issued = await op.issue_key(cc.CONSUMER_1, "sweep", idempotency_key="k1", reason=R)
        assert issued.secret and issued.org_id == org and not issued.replayed
        again = await op.issue_key(cc.CONSUMER_1, "sweep", idempotency_key="k1", reason=R)
        assert (again.secret, again.replayed, again.key_id) == (None, True, issued.key_id)
        tenant = await ops_.tenant(issued.secret)
        assert (tenant.auth.org_id, tenant.auth.user_id) == (org, cc.CONSUMER_1)
        before = (await tenant.balance()).available
        pins, card = await tenant.quote(v2fix.REQUESTED_MODEL)
        assert card == v2fix.BUILDERS["rate_card_marlin.json"]() and \
            pins.deployment_revision_id == IDS_PROD, (pins, card)
        adjusted = await op.adjust(cc.CONSUMER_1, "5.00000000", idempotency_key="a1", reason=R)
        assert await op.adjust(cc.CONSUMER_1, "5.00000000", idempotency_key="a1",
                               reason=R) == adjusted
        assert (await tenant.balance()).available == before + Credit("5.00000000")
        await op.set_suspension(org, "abuse", idempotency_key="s1", reason=R)
        with pytest.raises(errors.OrgSuspended):
            await op.issue_key(cc.CONSUMER_1, "ci", idempotency_key="k2", reason=R)
        await op.set_suspension(org, None, idempotency_key="s2", reason=R)
        await op.revoke_key(org, issued.key_id, idempotency_key="r1", reason=R)
        with pytest.raises(errors.InvalidApiKey):
            await ops_.tenant(issued.secret)
        # a tenant-scoped cancellation and the 24 h reconciliation of a CREDIT job
        store = PgJobStore(connect)
        world = type("World", (), {"clock": type("C", (), {"now": staticmethod(
            lambda: owner.execute("select infrx.now()").fetchone()[0])}),
            "ids": checks_admission._Ids()})()
        request = checks_admission.credit_request(world, checks_admission.C1_KEY, org)
        admitted = await store.admit_credit(request, b.idem(request, "cancel-me"))
        with pytest.raises(errors.NotFound):
            await op.cancel_job(b.ORG_A, admitted.job_handle, idempotency_key="c0", reason=R)
        cancelled = await op.cancel_job(org, admitted.job_handle, idempotency_key="c1",
                                        reason=R)
        assert (cancelled["state"], cancelled["cause"]) == ("cancelled", "client_cancelled")
        unknown = checks_admission.credit_request(world, checks_admission.C1_KEY, org)
        await store.admit_credit(unknown, b.idem(unknown, "unknown"))
        await store.prepared(await store.claim_preparation(unknown.request_id, "prep"))
        lease = await store.claim(unknown.request_id, "w")
        await PgStreamStore(connect).append(lease, b.events("published"))
        await store.complete_credit(lease, b.outcome(
            unknown.request_id, world, cause=b.TerminalCause.client_disconnected,
            state=b.JobState.failed, tokens=None, result_ref=None))
        with pytest.raises(errors.StateConflict):
            await op.reconcile(org, unknown.request_id, idempotency_key="rc0", reason=R)
        owner.execute("select infrx_test.advance(%s)", (DEFAULTS.unknown_usage_reconcile_s,))
        with pytest.raises(errors.NotFound):
            await op.reconcile(b.ORG_A, unknown.request_id, idempotency_key="rc1", reason=R)
        result = await op.reconcile(org, unknown.request_id, idempotency_key="rc2", reason=R)
        assert result["settlement"] == "released_platform_absorbed", result
    run(go())
    audits = owner.execute("select idempotency_key, count(*) from infrx.audit_entries where "
                           "idempotency_key in ('g1', 'k1', 'a1', 's1', 's2', 'r1', 'c1', "
                           "'rc2') group by 1 order by 1").fetchall()
    assert audits == [(k, 1) for k in sorted(("g1", "k1", "a1", "s1", "s2", "r1", "c1",
                                              "rc2"))], audits


IDS_PROD = v2fix.IDS.prod_deployment


def test_api_ops__a_partial_publication_is_unreachable_and_rerunnable() -> None:
    """G6B request 7 on the real registry: `service.publish` puts serving, deployment and
    card, THEN moves the alias - so a card refused by the registry (`Conflict`: a different
    card under an existing version) leaves the written rows unreachable (the label does
    not resolve) and no audit row, and re-running with a good card completes it and
    reaches it."""
    owner, _connect, ops_, secret = _operations()
    label = "d5-partial"
    serving, deployment, card = _release(label)
    clash = card.model_copy(update={"rate_card_version": v2fix.RATE_CARD_VERSION})
    model = f"{v2fix.PUBLIC_MODEL_ID}@{label}"
    catalog = PgCatalogDirectory(connector(pgharness.dsn(SERVICE_DB)))

    async def go():
        op = await ops_.operator(secret)
        with pytest.raises(errors.Conflict):
            await op.publish(serving, deployment, clash, model, idempotency_key="p1",
                             reason=R)
        assert await catalog.resolve(model, audience=CredentialAudience.consumer,
                                     endpoint_id=None) is None, "a partial publication resolves"
        assert await ops_.audit.by_idempotency_key("p1") is None
        done = await op.publish(serving, deployment, card, model, idempotency_key="p1",
                                reason=R)
        assert done["written"] == [False, False, True], done
        found = await catalog.resolve(model, audience=CredentialAudience.consumer,
                                      endpoint_id=None)
        assert found == deployment, found
    run(go())
