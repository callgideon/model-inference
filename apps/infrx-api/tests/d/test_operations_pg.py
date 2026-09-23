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
    owner.execute("update auth.users set email_confirmed_at = infrx.now() where id = %s",
                  (cc.CONSUMER_2,))
    identity = run(ops.PgSignup(ops._Db(connect)).verified_user(cc.CONSUMER_2))
    grant, replayed = run(ledger.grant_initial(identity, str(uuid.uuid4()), AT))
    assert (grant.wallet_id, replayed) == (wallet.wallet_id, True), (grant, replayed)
