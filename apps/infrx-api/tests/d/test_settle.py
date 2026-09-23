#!/usr/bin/env python3
"""D5: the terminal transaction (0018) against a REAL PostgreSQL (both images), check by
check - the same checks the D5 migration mutants must break.

    INFRX_D_TASK=d5 uv run --frozen pytest -q tests/d/test_settle.py
    INFRX_D_TASK=d5 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_settle.py

A `HarnessBusy` refusal means another checkout holds the D port: retry, never remove it.
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_admission, checks_settle, pgharness

DB = f"{pgharness.DATABASE}_settle"

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


# --- item 1: the legacy USD settling transaction --------------------------------------
def test_dur_settle__one_winner_exact_decimals_then_replay() -> None:
    print(checks_settle.check_settle_exact(_db()))


def test_dur_settle__a_different_proposal_after_terminal_is_already_terminal() -> None:
    print(checks_settle.check_settle_late_data(_db()))


def test_dur_settle__success_needs_this_jobs_stored_result() -> None:
    print(checks_settle.check_settle_result_ref(_db()))


def test_dur_settle__only_three_causes_charge_and_only_with_usage() -> None:
    print(checks_settle.check_settle_causes(_db()))


def test_dur_settle__over_envelope_is_a_free_platform_error() -> None:
    print(checks_settle.check_settle_envelope(_db()))


def test_dur_settle__published_without_usage_is_held_unknown() -> None:
    print(checks_settle.check_settle_unknown(_db()))


def test_dur_settle__everything_released_in_the_settling_transaction() -> None:
    print(checks_settle.check_settle_releases(_db()))


def test_dur_settle__timestamps_are_the_db_clock() -> None:
    print(checks_settle.check_settle_clock(_db()))


def test_dur_settle__the_terminal_event_is_the_triggers_one() -> None:
    print(checks_settle.check_settle_terminal_event(_db()))


# --- item 3: the cancel cause ------------------------------------------------------------
def test_dur_settle__cancel_records_its_cause_and_bills_by_r21() -> None:
    print(checks_settle.check_cancel_cause(_db()))


def test_dur_settle__an_unknown_cause_is_refused_and_changes_nothing() -> None:
    print(checks_settle.check_cancel_refuses_a_cause(_db()))


# --- item 5b: the released record --------------------------------------------------------
def test_dur_settle__a_24h_release_is_reported_as_released_not_as_a_new_terminal() -> None:
    print(checks_settle.check_settle_released(_db()))


# --- item 2: the CREDIT settlement and WorkV2 (D half) ------------------------------------
def test_credit_spend__settles_on_the_credit_wallet_at_the_admitted_card() -> None:
    print(checks_settle.check_credit_settle(_db()))


def test_credit_spend__sql_settle_equals_v2_settle_on_the_grid() -> None:
    print(checks_settle.check_credit_grid(_db()))


def test_credit_spend__usd_wallet_untouched() -> None:
    print(checks_settle.check_credit_usd_untouched(_db()))


def test_credit_spend__regimes_never_cross() -> None:
    print(checks_settle.check_credit_regimes(_db()))


def test_credit_rate__a_card_published_after_admission_is_ignored() -> None:
    print(checks_settle.check_credit_rate(_db()))


def test_credit_rate__retired_wallet_still_settles() -> None:
    print(checks_settle.check_credit_retired(_db()))


# --- R91 (G2): the read-only lookup over D2's mapping -----------------------------------------
def test_dur_admit__lookup_reads_the_mapped_job_and_writes_nothing() -> None:
    print(checks_settle.check_lookup(_db()))
