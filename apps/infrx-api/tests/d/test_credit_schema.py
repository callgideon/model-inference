#!/usr/bin/env python3
"""D1R Layer 2: the additive CREDIT / provider schema against a REAL PostgreSQL.

Runs in the D harness's own labelled container (E2R), on the pinned postgres 16 image
with the Supabase shim, or on the real `supabase/postgres` 17.6 image with no shim:

    uv run --frozen pytest -q tests/d/test_credit_schema.py
    INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_credit_schema.py

A `HarnessBusy` refusal means another checkout holds the port: retry, never remove it.
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_credit, pgharness

UPGRADE05_DB = f"{pgharness.DATABASE}_upgrade05"

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

_state: dict = {}


def _upgraded05():
    """0001-0005 with users, positive/negative/violating USD history and accepted
    old-regime jobs, then everything D1R adds."""
    if "upgrade05" not in _state:
        pgharness.ensure()
        _state["upgrade05"] = checks_credit.upgrade05(
            pgharness, UPGRADE05_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    return _state["upgrade05"]


# --- item 1: inventory and upgrade from the exact 0005 schema --------------------
def test_d1r_leaves_the_0001_0005_schema_unchanged() -> None:
    """Every constraint, trigger, policy, function body, ACL and column of 0001-0005 -
    the USD wallet trigger and the narrow financial RPC grants included - survives D1R
    unchanged, except the named allowances."""
    conn, before = _upgraded05()
    print(checks_credit.check_legacy_schema_unchanged(conn, before["inventory"]))


def test_upgrade05_preserves_usd_history_and_old_regime_jobs() -> None:
    """USD history (positive, negative and sign-violating), accepted old-regime jobs,
    their USD holds and the USD wallet summaries are value-identical after D1R."""
    conn, before = _upgraded05()
    print(checks_credit.check_old_regime_preserved(conn, before))
