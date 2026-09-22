#!/usr/bin/env python3
"""D3: fenced leases, recovery and cancellation (0016) against a REAL PostgreSQL (both
images), check by check - the same checks the D3 migration mutants must break.

    uv run --frozen pytest -q tests/d/test_leases.py
    INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_leases.py

A `HarnessBusy` refusal means another checkout holds the D port: retry, never remove it.
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_admission, checks_leases, pgharness

DB = f"{pgharness.DATABASE}_leases"

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


# --- item 1: atomic claim, generation increment, fenced heartbeat on the DB clock ---------
def test_claim__one_generation_under_the_job_lock_with_its_phase_instants() -> None:
    print(checks_leases.check_claim_generation(_db()))


def test_fence__stale_foreign_expired_refused_stored_lease_renewed_deadline_first() -> None:
    print(checks_leases.check_fence(_db()))


# --- item 4: the preparation lease through the same fence ----------------------------------
def test_fence__a_preparation_lease_renews_within_its_phase_and_terminalizes_after() -> None:
    print(checks_leases.check_preparation_fence(_db()))


# --- item 2: cancellation releases through terminalization ---------------------------------
def test_cancel__every_state_terminalizes_and_releases_in_one_transaction() -> None:
    print(checks_leases.check_cancel(_db()))


# --- item 3: the reaper -------------------------------------------------------------------------
def test_recover__requeues_before_publication_only_within_the_retry_counter() -> None:
    print(checks_leases.check_recover_requeue(_db()))


def test_recover__a_lost_preparation_is_redispatched_then_settled() -> None:
    print(checks_leases.check_recover_preparation(_db()))


def test_recover__unknown_usage_is_released_platform_absorbed_at_the_window() -> None:
    print(checks_leases.check_recover_unknown_release(_db()))


def test_recover__one_unreapable_job_never_stops_the_sweep() -> None:
    print(checks_leases.check_recover_isolation(_db()))


# --- item 5: the boundary surface ---------------------------------------------------------------
def test_privileges__service_operations_and_internal_bodies() -> None:
    print(checks_leases.check_lease_privileges(_db()))


# --- item 6: the concurrency check the migration mutants run (committed rows: own DB) --------
def test_races__claim_heartbeat_and_cancel_serialize_on_the_job_row() -> None:
    _db()
    race_db = f"{DB}_race"
    pgharness.recreate(race_db)
    pgharness.apply(race_db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(race_db) as conn:
        checks_admission.seed_admission(conn)
    print(checks_leases.check_lease_races(pgharness.connect, race_db))
