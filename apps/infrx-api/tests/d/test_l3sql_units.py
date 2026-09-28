#!/usr/bin/env python3
"""L3-SQL: the Python half of `PgControlStore`, with NO database - what it sends to the RPCs
of `0032_lab_control.sql` and what it never queries. `code_mutants_l3sql.py`'s Python list
runs here; the SQL is `test_l3sql_control.py`.

    uv run --frozen pytest -q tests/d/test_l3sql_units.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from infrx.contracts.v2.money_units import Credit
from infrx.contracts.v2.records import (DeploymentState, Environment, RateCardSnapshot)
from infrx.state.lab_control import PgControlStore

from .test_adapter_units import _Conn
from .test_d7_units import NEMO, _ok, _sent

D = "c0000006-0000-4000-8000-000000000006"
T = datetime(2026, 9, 27, tzinfo=timezone.utc)
ROW = {"deployment_revision_id": D, "endpoint_id": D, "provider_org_id": NEMO,
       "serving_version_id": D, "environment": "dev", "visibility": "private",
       "state": "ready_private", "max_input_tokens": 1, "max_output_tokens": 1,
       "created_at": T.isoformat()}
LISTING = {"public_model_id": "m", "version": 2, "deployment_revision_id": D,
           "rate_card_version": "c"}
ENTRY = {"entry_id": D, "wallet_id": D, "wallet_kind": "provider_dev",
         "kind": "operator_allocation", "operation_id": D, "request_id": None, "actor": "ops",
         "reason": "r", "created_at": T.isoformat(), "amount": "12.50000000"}


def _store(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgControlStore(connect), conn


def test_calls__carry_the_servers_identity_and_the_cas_expectations() -> None:
    card = RateCardSnapshot(rate_card_version="c", model_id=D, deployment_revision_id=D,
                            serving_version_id=D, input_rate_per_million="3",
                            output_rate_per_million="9", effective_at=T, approved_by="ops")
    store, conn = _store({"endpoint_id": D}, ROW, {"key_id": D}, LISTING, LISTING,
                         {"entry": ENTRY, "replayed": False}, [])
    _ok(store.endpoint(NEMO, "dev", Environment.dev, "a"))
    moved = _ok(store.transition(D, NEMO, expected=DeploymentState.validating,
                                 to=DeploymentState.ready_private, actor="a", reason="r"))
    _ok(store.issue_dev_key(provider_org_id=NEMO, endpoint_id=D, user_id="u", key_hash="h",
                            prefix="p", name="n"))
    listing = _ok(store.publish("m", card, expected_version=1, actor="ops", reason="r"))
    _ok(store.rollback("m", to_version=1, expected_version=2, actor="ops", reason="r"))
    entry = _ok(store.fund_dev_wallet(NEMO, Credit("12.5"), operation_id=D, actor="ops",
                                      reason="r"))
    _ok(store.events(NEMO))
    assert (moved.state, listing.version, str(entry.amount)) == \
        (DeploymentState.ready_private, 2, "12.50000000")
    assert [_sent(conn, n) for n in range(7)] == [
        ("lab_control_endpoint", {"provider_org_id": NEMO, "name": "dev", "environment": "dev",
                                  "actor": "a"}),
        ("lab_control_transition", {"deployment_revision_id": D, "provider_org_id": NEMO,
                                    "expected": "validating", "to": "ready_private",
                                    "actor": "a", "reason": "r"}),
        ("lab_control_dev_key", {"provider_org_id": NEMO, "endpoint_id": D, "user_id": "u",
                                 "key_hash": "h", "prefix": "p", "name": "n"}),
        ("lab_control_publish", {"public_model_id": "m", "expected_version": 1,
                                 "actor": "ops", "reason": "r", "card": {
                                     "rate_card_version": "c", "model_id": D,
                                     "deployment_revision_id": D, "serving_version_id": D,
                                     "input_rate_per_million": "3.00000000",
                                     "output_rate_per_million": "9.00000000",
                                     "effective_at": T.isoformat(), "approved_by": "ops"}}),
        ("lab_control_rollback", {"public_model_id": "m", "to_version": 1,
                                  "expected_version": 2, "actor": "ops", "reason": "r"}),
        ("lab_control_fund", {"provider_org_id": NEMO, "amount": "12.50000000",
                              "operation_id": D, "actor": "ops", "reason": "r"}),
        ("lab_control_events", {"provider_org_id": NEMO})]


def test_reads__a_malformed_id_is_absent_without_a_query() -> None:
    store, conn = _store()
    assert _ok(store.deployment("nope")) is None
    assert _ok(store.model_provider("nope")) is None
    assert _ok(store.endpoint_alias("nope")) is None
    assert conn.sent == [], conn.sent
