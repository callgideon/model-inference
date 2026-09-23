#!/usr/bin/env python3
"""D5 items 7-8: the Python half of the operator adapters and the catalog, with NO database
- what the adapter decides: the tenant predicate and parameters it binds, a replay read as
a replay, a lookup keyed by its own key, the unit each usage row keeps (R73), CREDIT-only
holds, the ledger movement it asks for, a private deployment only for its provider, and a
database error raised - never answered as `None`. `tests/d/code_mutants_d5.py` runs here
(no Docker); the SQL semantics are `test_operations_pg.py` / `test_catalog_pg.py`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.money_units import Credit
from infrx.contracts.v2.records import CredentialAudience
from infrx.state import catalog as cat
from infrx.state import operations as ops

from .test_adapter_units import _Conn, _db_error

AT = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
KEY = ("a0000009-0000-4000-8000-000000000009", b.ORG_A, "consumer", "h" * 64,
       "sk-infrx-abcdefgh", "sweep", "a0000001-0000-4000-8000-000000000001", AT, None)


def _ok(coro):
    try:
        return asyncio.run(coro)
    except Exception as failed:
        raise AssertionError(f"{type(failed).__name__}: {failed}") from None


def _with(cls, *answers):
    """An adapter whose every statement answers the next scripted rows (a list) or error."""
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return cls(connect), conn


def test_tenant_store__binds_the_organization_and_reads_a_replay_as_one() -> None:
    tenants, conn = _with(ops.PgTenantStore, [KEY], [], [], [KEY])
    row = _ok(tenants.key(b.ORG_A, KEY[0]))
    sql, params = conn.sent[0]
    assert "org_id = %s" in sql and params == (KEY[0], b.ORG_A), (sql, params)
    assert (row.key_id, row.org_id, row.audience) == (KEY[0], b.ORG_A,
                                                      CredentialAudience.consumer)
    assert _ok(tenants.key(b.ORG_B, KEY[0])) is None
    assert _ok(tenants.insert_key(row)) is False, "a replayed insert was reported as written"
    sql, _ = conn.sent[2]
    assert "on conflict (id) do nothing" in sql, sql
    assert _ok(tenants.insert_key(row)) is True


def test_tenant_store__revocation_keeps_the_first_instant_and_suspension_sends_the_code() -> None:
    tenants, conn = _with(ops.PgTenantStore, [KEY], [], [(True,)], [(False,)])
    _ok(tenants.revoke_key(b.ORG_A, KEY[0], AT))
    sql, params = conn.sent[0]
    assert "coalesce(revoked_at, infrx.now())" in sql and params == (KEY[0], b.ORG_A), sql
    with pytest.raises(errors.NotFound):
        asyncio.run(tenants.revoke_key(b.ORG_B, KEY[0], AT))
    _ok(tenants.set_suspension(b.ORG_A, "abuse", AT))
    _ok(tenants.set_suspension(b.ORG_A, None, AT))
    assert (conn.sent[2][1][:3], conn.sent[3][1][:3]) == \
        ((b.ORG_A, True, "abuse"), (b.ORG_A, False, None)), (conn.sent[2], conn.sent[3])


def test_audit_log__looks_up_by_its_own_key() -> None:
    audit, conn = _with(ops.PgAuditLog, [])
    assert _ok(audit.by_idempotency_key("k-1")) is None
    sql, params = conn.sent[0]
    assert "audit_by_idempotency_key(%s)" in sql and params == ("k-1",), (sql, params)


def test_account_view__each_row_keeps_its_unit_and_holds_are_credit_only() -> None:
    usd = (b.ORG_A, b.ORG_A, "legacy_usd", "USD", "0.00044400", 1200, 340, "authoritative",
           "settled", None, None, None, "pv_test", AT)
    credit = (b.ORG_B, b.ORG_A, "credit", "CREDIT", "0.88800000", 1200, 340, "authoritative",
              "settled", v2fix.RATE_CARD_VERSION, v2fix.IDS.serving_version,
              v2fix.IDS.prod_deployment, None, AT)
    view, conn = _with(ops.PgAccountView, [usd, credit], [(b.ORG_B, "held", Decimal("1.5"))])
    history = _ok(view.usage(b.ORG_A))
    assert history.totals() == {"CREDIT": "0.88800000", "USD": "0.00044400"}, history.totals()
    holds = _ok(view.holds(b.ORG_A))
    assert [(h.request_id, h.amount) for h in holds] == [(b.ORG_B, Credit("1.5"))], holds
    sql, params = conn.sent[1]
    assert "accounting_regime = 'credit'" in sql and params == (b.ORG_A,), sql


def test_ledger__asks_for_an_operator_adjustment_and_answers_the_entry() -> None:
    entry = {"entry_id": v2fix.IDS.request, "wallet_id": v2fix.IDS.consumer_wallet,
             "wallet_kind": "consumer", "kind": "operator_adjustment", "amount": "2.50000000",
             "operation_id": v2fix.IDS.signup_operation, "request_id": None,
             "actor": "ops@test", "reason": "goodwill", "created_at": AT.isoformat()}
    ledger, conn = _with(ops.PgLedger, {"entry": entry, "replayed": True},
                         {"settlement_state": "released_platform_absorbed", "replayed": False})
    wallet = v2fix.BUILDERS["wallet_consumer.json"]()
    got, replayed = _ok(ledger.adjust(wallet, Credit("2.5"), v2fix.IDS.signup_operation,
                                      "ops@test", "goodwill", AT))
    sent = conn.sent[0][1][0].obj
    assert (sent["kind"], sent["amount"], sent["wallet_id"], sent["actor"]) == \
        ("operator_adjustment", "2.50000000", wallet.wallet_id, "ops@test"), sent
    assert (str(got.amount), replayed) == ("2.50000000", True), (got, replayed)
    state = _ok(ledger.reconcile(b.ORG_A, v2fix.IDS.request, "op", "ops@test", AT))
    assert state == "released_platform_absorbed"
    sent = conn.sent[1][1][0].obj
    # review CF-2: the caller is the actor on both movements (the ledger row and the audit
    # row the SQL writes carry it; test_operations_pg reads them back)
    assert (sent["org_id"], sent["actor"], sent["operation_id"]) == (b.ORG_A, "ops@test", "op"), \
        sent


def test_registry__the_alias_moves_at_the_deployments_newest_effective_card() -> None:
    """Review CF-1: the listing's card - what every new admission pins and pays - is the
    deployment's NEWEST card effective at the database clock (`test_catalog_pg`'s alias
    move proves it on PostgreSQL with two cards); the alias without its `@label`."""
    registry, conn = _with(ops.PgRegistry, [(1,)])
    _ok(registry.move_alias(f"{v2fix.PUBLIC_MODEL_ID}@2026-09-01", v2fix.IDS.prod_deployment))
    sql, params = conn.sent[0]
    card = sql[sql.index("select c.rate_card_version"):sql.index("as card")]
    assert "c.effective_at <= infrx.now()" in card and \
        "order by c.effective_at desc, c.created_at desc limit 1" in card, card
    assert params == {"alias": v2fix.PUBLIC_MODEL_ID,
                      "deployment": v2fix.IDS.prod_deployment}, params


DEV = (v2fix.IDS.dev_deployment, v2fix.IDS.dev_endpoint, v2fix.IDS.provider_org,
       v2fix.IDS.serving_version, "dev", "private", "ready_private", 30720, 2048, v2fix.T0)


def test_catalog__a_private_deployment_only_for_its_provider_and_errors_raised() -> None:
    consumer, _ = _with(cat.PgCatalogDirectory, [], [DEV])      # no public row; a private one
    assert _ok(consumer.resolve(v2fix.DEV_REQUESTED_MODEL,
                                audience=CredentialAudience.consumer,
                                endpoint_id=v2fix.IDS.dev_endpoint)) is None, \
        "a consumer reached a private deployment"
    provider, conn = _with(cat.PgCatalogDirectory, [], [DEV])
    found = _ok(provider.resolve(v2fix.DEV_REQUESTED_MODEL,
                                 audience=CredentialAudience.provider_dev,
                                 endpoint_id=v2fix.IDS.dev_endpoint))
    assert found.deployment_revision_id == v2fix.IDS.dev_deployment, found
    assert conn.sent[1][1]["endpoint"] == v2fix.IDS.dev_endpoint, conn.sent[1]
    failing, _ = _with(cat.PgCatalogDirectory, _db_error("57014", "canceling statement"))
    try:
        asyncio.run(failing.active_rate_card(v2fix.IDS.prod_deployment))
    except AssertionError:
        raise
    except Exception:
        pass
    else:
        raise AssertionError("a database error was answered as None")
    card, conn = _with(cat.PgCatalogDirectory, [])
    assert _ok(card.active_rate_card(v2fix.IDS.dev_deployment)) is None
    assert "effective_at <= infrx.now()" in conn.sent[0][0], conn.sent[0][0]


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
