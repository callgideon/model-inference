#!/usr/bin/env python3
"""D1 Layer 2: the pilot migrations against a REAL PostgreSQL.

Not a unit test. It starts the task-local container `infrx-d1-postgres` (08 §8,
pinned by digest), applies `0001..0004` to two databases - one empty, one seeded with
a copy of the current schema's data - and asserts the invariants in `checks.py`.
A missing Docker is reported as a skip naming the reason, never as a pass.

    uv run --frozen pytest -q tests/d                        # this suite
    uv run --frozen pytest -q tests/d/test_schema_postgres.py -k dur_rls

The same `checks.py` functions are what `test_migration_mutants.py` runs against
single-edit mutants of the migrations (R32).
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks, pgharness

UPGRADE_DB = f"{pgharness.DATABASE}_upgrade"
VOLUME_DB = f"{pgharness.DATABASE}_volume"   # seed_volume's 3,000 rows stay out of the shared one
PRODLIKE_DB = "prodlike_d1"                 # deliberately not infrx_*: see check_production_clock

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")

_state: dict = {}


def _fresh(database: str = pgharness.DATABASE):
    """The fresh-apply database, built once and shared by the read-only checks.

    A check that WRITES (seed_volume) asks for its own database name, so what it writes
    can never reach a read-only check that happens to run after it."""
    if database not in _state:
        pgharness.ensure()
        pgharness.recreate(database)
        pgharness.apply(database, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(database)
        checks.seed_fixtures(conn)
        _state[database] = conn
    return _state[database]


def _upgraded():
    """A seeded copy of the CURRENT schema, then upgraded."""
    if "upgraded" not in _state:
        pgharness.ensure()
        pgharness.recreate(UPGRADE_DB)
        current = tuple(f for f in migrations.sql_for(shim=pgharness.NEEDS_SHIM)
                        if f[0] in ("supabase_shim.sql", "0001_init.sql",
                                    "0002_seed_models.sql"))
        pgharness.apply(UPGRADE_DB, current)
        conn = pgharness.connect(UPGRADE_DB)
        before = checks.seed_legacy(conn)
        pilot = tuple(f for f in migrations.sql_for(shim=pgharness.NEEDS_SHIM) if f[0] not in
                      ("supabase_shim.sql", "0001_init.sql", "0002_seed_models.sql"))
        pgharness.apply(UPGRADE_DB, pilot)
        _state["upgraded"] = (conn, before)
    return _state["upgraded"]


# --- (1) fresh-database apply -------------------------------------------------
def test_migration_applies_to_an_empty_database() -> None:
    """0001..0004 apply in order to an empty database and create every relation of 06,
    each with RLS enabled and no grant reachable from a browser role."""
    print(checks.check_relations_exist(_fresh()))


def test_dur_cap__money_is_one_domain() -> None:
    """Every money column is numeric(20,8): the R11 domain, in the database."""
    print(checks.check_money_domain(_fresh()))


def test_dur_cap__wallets_reconcile_and_new_orgs_start_at_zero() -> None:
    """A wallet summary equals the immutable ledger plus active holds, and a new
    organization gets a zero wallet and no automatic grant (02)."""
    print(checks.check_wallets_and_reconciliation(_fresh()))


def test_entitlements_distinguish_default_from_nothing() -> None:
    """R24: `model_ids` null is the platform default, `{}` entitles nothing - so the
    column is nullable with no default. And `models.limits` is extended, not duplicated."""
    print(checks.check_entitlements_and_limits(_fresh()))


# --- (2) upgrade from a seeded copy of the current schema ----------------------
def test_upgrade_preserves_every_historical_value() -> None:
    """Every balance and usage value is identical after the upgrade, the precision
    change added only trailing zeros, wallets are imported from the ledger, and the
    history is marked outside the settlement regime so it can never be re-debited."""
    conn, before = _upgraded()
    print(checks.check_upgrade_preserved(conn, before))


def test_upgrade_reconciliation_is_clean() -> None:
    """06's reconciliation query reports no drift on the upgraded database."""
    conn, _ = _upgraded()
    print(checks.check_wallets_and_reconciliation(conn))


# --- (3) role attack matrix (DUR-RLS) -----------------------------------------
def test_dur_rls__every_impersonated_session_is_somebody() -> None:
    """A matrix where `auth.uid()` is NULL denies everything and proves nothing."""
    print(checks.check_sessions_are_somebody(_fresh()))


def test_dur_rls__browser_roles_cannot_reach_protected_state() -> None:
    """Every protected column, relation and mutation boundary, against anon, a member,
    an owner and a platform operator session - each denial asserted, with positive
    controls so a matrix that denies everything cannot pass."""
    print(checks.check_role_matrix(_fresh()))


def test_dur_rls__the_mutation_boundary_is_narrow_and_fails_closed() -> None:
    """06's eleven RPCs are SECURITY DEFINER with a fixed search_path, executable by
    `service_role` alone, and raise until their owning task implements them."""
    print(checks.check_rpc_boundary(_fresh()))


def test_dur_rls__the_browser_privilege_surface_is_enumerated() -> None:
    """Ruling 4: `anon` and `authenticated` hold exactly the enumerated grants - verb by
    verb, column by column - and nothing in schema `infrx`. Enumerating only
    insert/update/delete is what left TRUNCATE behind (B3)."""
    print(checks.check_privileges(_fresh()))


def test_dur_rls__the_execute_surface_is_enumerated() -> None:
    """N4: `revoke all … from public` leaves Supabase's default-ACL grant to `anon` and
    `authenticated` in place, and the per-schema default-privilege revoke for functions is
    a no-op - so every function, every sequence and a function created afterwards are
    checked against the enumerated caller list."""
    print(checks.check_function_privileges(_fresh()))


def test_dur_rls__no_operator_identity_is_customer_readable() -> None:
    """Ruling 2 / B2: the operator principal and their prose live only where a customer
    cannot SELECT them - structurally, not behind a column grant on a legacy table."""
    print(checks.check_no_operator_identity_in_public(_fresh()))


def test_dur_rls__truncate_is_refused_for_every_role() -> None:
    """B3: TRUNCATE ignores RLS and never fires a row trigger, so the append-only
    relations refuse it with a statement trigger as well as a missing privilege."""
    print(checks.check_truncate_refused(_fresh()))


def test_dur_rls__a_leaky_function_cannot_read_another_tenant() -> None:
    """B5 / ruling 6: `security_barrier`, probed the way the reviewer broke it."""
    print(checks.check_leaky_function_probe(_fresh()))


def test_dur_cap__a_legacy_ledger_writer_cannot_drift_the_wallet() -> None:
    """B8 / ruling 8: the deployed console's addCredit moves the wallet total in the
    same transaction, so no writer can make the summary disagree with the ledger."""
    print(checks.check_legacy_writer_does_not_drift(_fresh()))


def test_dur_rls__the_console_read_surface_is_tenant_scoped() -> None:
    """0005's views read `infrx` with the view owner's rights, so each one carries its
    own tenant or operator predicate: a member sees only their organization, a
    non-operator sees no operator relation, a customer reads `platform` as the actor of
    anything the platform did, no feedback list carries a calibration label, and
    `org_wallet_summary` refuses another organization instead of answering empty."""
    print(checks.check_console_read_surface(_fresh()))


# --- (4) row checks -----------------------------------------------------------
def test_every_row_check_refuses_its_violation() -> None:
    """One statement per invariant the schema claims; each must be refused."""
    print(checks.check_row_constraints(_fresh()))


# --- (5) bounded access paths -------------------------------------------------
def test_bounded_access_paths_use_their_index() -> None:
    """Org/time/id pagination, pending outbox, expiring leases, aged holds and the
    journal cursor are index-served on a seeded database, with no sequential scan."""
    conn = _fresh(VOLUME_DB)
    if "volume" not in _state:
        checks.seed_volume(conn)
        _state["volume"] = True
    print(checks.check_index_plans(conn))
    print(checks.check_view_pushdown(conn))


# --- the database clock (R7 / S1 note 3) --------------------------------------
def test_database_clock_moves_only_in_a_task_local_database() -> None:
    """The offset is honoured in `infrx_<task>` and ignored everywhere else, even with
    the fixture installed - so no deployed process can move the store's clock."""
    print(checks.check_test_clock(_fresh(), pgharness.connect))
    pgharness.recreate(PRODLIKE_DB)
    pgharness.apply(PRODLIKE_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(PRODLIKE_DB) as prod:
        print(checks.check_production_clock(prod))
