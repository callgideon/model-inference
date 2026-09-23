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
