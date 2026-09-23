#!/usr/bin/env python3
"""D4: the stream journal (0017) against a REAL PostgreSQL (both images), check by check -
the same checks the D4 migration mutants must break (`migration_mutants.D4_MUTANTS`).

    INFRX_D_TASK=d4 uv run --frozen pytest -q tests/d/test_journal.py
    INFRX_D_TASK=d4 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_journal.py

A `HarnessBusy` refusal means another run holds this task's port: retry, never remove it.
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_admission, checks_journal, pgharness

DB = f"{pgharness.DATABASE}_journal"

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


# --- item 1: the fenced append ------------------------------------------------------------
def test_append__fence_first_then_batch_then_marker() -> None:
    print(checks_journal.check_append(_db()))


def test_append__oversize_event_refused_whole() -> None:
    print(checks_journal.check_append_oversize(_db()))


def test_append__terminal_event_refused_anywhere_in_a_batch() -> None:
    print(checks_journal.check_append_terminal_refused(_db()))


def test_append__job_ceiling_holds_back_the_terminal_reserve() -> None:
    print(checks_journal.check_append_job_ceiling(_db()))


def test_append__empty_batch_neither_writes_nor_publishes() -> None:
    print(checks_journal.check_append_empty(_db()))


def test_append__stale_foreign_expired_and_preparation_leases_store_nothing() -> None:
    print(checks_journal.check_append_fenced(_db()))


def test_append__past_the_generation_instant_append_terminalizes_and_refuses() -> None:
    print(checks_journal.check_append_past_the_instant(_db()))


# --- item 2: the global journal budget without a global lock -------------------------------
def test_append__never_moves_the_global_charge() -> None:
    print(checks_journal.check_global_charge(_db()))


# --- item 3: one terminal event for every terminalization ----------------------------------
def test_terminal__every_path_writes_exactly_one_event_last() -> None:
    print(checks_journal.check_terminal_every_path(_db()))


# --- item 4: replay ---------------------------------------------------------------------------
def test_read__replay_equals_committed_rows() -> None:
    print(checks_journal.check_read_replay(_db()))


def test_read__tenant_bound() -> None:
    print(checks_journal.check_read_tenant(_db()))


def test_read__bounded_and_paged() -> None:
    print(checks_journal.check_read_bounded(_db()))


def test_read__gap_expired_and_past_head_are_typed() -> None:
    print(checks_journal.check_read_typed(_db()))
