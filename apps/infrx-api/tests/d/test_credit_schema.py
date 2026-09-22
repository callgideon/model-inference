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
CREDIT_DB = f"{pgharness.DATABASE}_credit"
VOLUME_DB = f"{pgharness.DATABASE}_credit_volume"

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


# --- item 2: CREDIT wallets, ledger, entitlements, the initial grant -------------
def _credit():
    """A fresh database with D1's fixture plus the CREDIT individuals and two grants."""
    if "credit" not in _state:
        from . import checks
        pgharness.ensure()
        pgharness.recreate(CREDIT_DB)
        pgharness.apply(CREDIT_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(CREDIT_DB)
        checks.seed_fixtures(conn)
        checks_credit.seed_credit(conn)
        _state["credit"] = conn
    return _state["credit"]


def test_credit_identity__ownership_kind_unit_and_ledger_rules() -> None:
    """CREDIT-IDENTITY, R59-7, R67, R71 as refused statements, with controls."""
    print(checks_credit.check_credit_identity(_credit()))
    print(checks_credit.check_credit_reconciles(_credit()))


def test_credit_grant__one_transaction_idempotent_and_fail_closed() -> None:
    """CREDIT-GRANT and the A1 seam: one +10000.00000000 row per individual, every retry
    answers the same grant, a disabled flag is a maintenance refusal."""
    print(checks_credit.check_grant(_credit()))


def test_credit_grant__a_race_issues_exactly_one() -> None:
    """CREDIT-GRANT: eight concurrent callers for one individual, one ledger row."""
    _credit()
    print(checks_credit.check_grant_race(pgharness.connect, CREDIT_DB))
    print(checks_credit.check_credit_reconciles(_credit()))


def test_credit_units__no_conversion_and_explicit_regimes() -> None:
    """CREDIT-UNITS (R64/R65/R73): no cross-unit function or view, the money_unit_cases
    fixture as SQL, and a usage row's regime fixes which amount it may carry."""
    conn = _credit()
    print(checks_credit.check_no_unit_conversion(conn))
    print(checks_credit.check_money_unit_cases(conn))
    print(checks_credit.check_regime_on_usage(conn))


# --- item 3: the provider / model / serving / deployment / listing / rate registry --
def test_registry__the_operator_seed_is_the_f2p_fixtures() -> None:
    """The documented operator seed works without Lab, is idempotent, and its rows are
    the F2P fixture records field for field, with the card labelled provisional (P-01)."""
    print(checks_credit.check_seed_is_the_fixtures(_credit()))


def test_registry__ownership_immutability_and_visibility() -> None:
    """Provider-owned, immutable revisions and rate snapshots; dev never public; roles
    distinct from consumer roles; display text is not a key."""
    print(checks_credit.check_registry(_credit()))


# --- item 4: pin resolution, admission rows, the read surface, privileges ---------
def test_resolve_admission_pins__alias_and_pin_are_the_fixture() -> None:
    """D2 seam: alias and R62 pin resolve to the F2P admission_pins; unknown, private
    dev, retired and draining are not_found; a card not yet effective is unpriced."""
    print(checks_credit.check_resolve_pins(_credit()))


def test_credit_admission__pins_holds_and_settlement_rows() -> None:
    """The atomic admission schema D2 writes and D5 settles, as refused/accepted rows."""
    print(checks_credit.check_credit_admission_rows(_credit()))


def test_credit_rate__a_published_rate_never_reaches_an_admitted_job() -> None:
    """CREDIT-RATE: publish a new card and listing while a job is queued."""
    print(checks_credit.check_credit_rate(_credit()))


def test_credit_read_surface__exact_text_scoped_and_legacy_separate() -> None:
    """C0's result shapes: wallet summary, wallet/ledger pages, legacy USD statement."""
    print(checks_credit.check_credit_read_surface(_credit()))
    print(checks_credit.check_credit_leaky_probe(_credit()))


def test_credit_privileges__service_reads_money_and_writes_through_seams() -> None:
    """R59-4 for the new relations, plus D1's enumerated surfaces on the full chain."""
    from . import checks
    conn = _credit()
    print(checks_credit.check_credit_privileges(conn))
    print(checks.check_privileges(conn))
    print(checks.check_function_privileges(conn))


def test_credit_role_matrix__provider_consumer_operator_service_anon() -> None:
    """DUR-RLS extended to provider member / provider admin / consumer-only / operator /
    service / anon."""
    print(checks_credit.check_credit_role_matrix(_credit()))


# --- item 5: fail closed, flags, re-run -----------------------------------------
def test_fail_closed__a_disabled_or_missing_flag_is_maintenance() -> None:
    """Application is not enablement: every guarded write refuses with 55000."""
    print(checks_credit.check_fail_closed(_credit()))


def test_flags__applying_the_migrations_enables_nothing() -> None:
    conn, _ = _upgraded05()
    print(checks_credit.check_flag_defaults(conn))


def test_legacy_read_path__deployed_console_and_admin_reads_still_execute() -> None:
    """The deployed console's reads, the admin page's reads and the legacy USD writers
    work unchanged on the upgraded database."""
    conn, before = _upgraded05()
    print(checks_credit.check_legacy_read_path(conn, before))


def test_rerun__applying_d1r_twice_is_a_no_op() -> None:
    """Upgrade re-run: a second application changes no object, row or flag. Runs last on
    the upgrade database (it enables a flag and grants)."""
    conn, _ = _upgraded05()
    _, d1r = checks_credit.split(migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    print(checks_credit.check_rerun_is_noop(
        conn, lambda: pgharness.apply(UPGRADE05_DB, d1r)))


# --- item 6: the seams -----------------------------------------------------------
def test_seams__the_map_handed_to_a1_d2_c0_is_the_catalog() -> None:
    print(checks_credit.check_seams(_credit()))


# --- query plans at 10^5 rows per tenant --------------------------------------------
def test_plans__credit_ledger_keyset_at_realistic_tenant_size() -> None:
    """10^5 ledger rows in each of two tenants; the page and next page are ordered
    index ranges; the barrier view pushes the wallet qual down."""
    from . import checks
    pgharness.ensure()
    pgharness.recreate(VOLUME_DB)
    pgharness.apply(VOLUME_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(VOLUME_DB) as conn:
        checks.seed_fixtures(conn)
        checks_credit.seed_credit(conn)
        checks_credit.seed_credit_volume(conn)
        print(checks_credit.check_credit_plans(conn))
        print(checks_credit.check_credit_reconciles(conn))
