#!/usr/bin/env python3
"""D2 item 1: the atomic admission against a REAL PostgreSQL (both images).

    uv run --frozen pytest -q tests/d/test_admission.py
    INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_admission.py

A `HarnessBusy` refusal means another checkout holds the D port: retry, never remove it.
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import checks_admission, pgharness

DB = f"{pgharness.DATABASE}_admission"

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


def test_admission__one_acceptance_owns_one_job_hold_reservations_dispatch() -> None:
    """DUR-ADMIT: USD and CREDIT acceptances, row by row (pins, wallet, derived hold)."""
    print(checks_admission.check_admission_accepts(_db()))


def test_admission__every_refusal_is_typed_and_owns_nothing() -> None:
    """DUR-ADMIT / R10 / R29 / R45 / R55 / R66 / R69 / R70: rejects own none."""
    print(checks_admission.check_admission_refusals(_db()))


def test_admission__each_capacity_scope_refuses_on_its_own() -> None:
    """DUR-CAP: total, org, key, preparation (R1) and journal."""
    print(checks_admission.check_admission_capacity(_db()))


def test_admission__replay_conflict_and_the_terminal_tombstone() -> None:
    """DUR-ADMIT: replay identity, 409, R6, 410 exactly at terminal + 24 h."""
    print(checks_admission.check_admission_idempotency(_db()))


def test_admission__concurrent_admissions_and_grants_keep_the_lock_order() -> None:
    """DUR-CAP: 8 concurrent admissions + 2 grants across two orgs/keys, 5 rounds."""
    _db()
    print(checks_admission.check_admission_concurrency(pgharness.connect, DB))


# --- item 2: preparation and the dispatch outbox, in SQL ---------------------------
def test_prepare__fenced_preparing_to_queued_with_one_inference_dispatch() -> None:
    """02 §3 / R46 / R38: the prepare boundary, fence by fence."""
    from . import checks_dispatch
    print(checks_dispatch.check_prepare_transition(_db()))


def test_prepare__the_preparation_deadline_terminalizes_in_the_same_call() -> None:
    """R29 / R39 / R55: claim, late prepared and the retry bound terminalize and refuse."""
    from . import checks_dispatch
    print(checks_dispatch.check_preparation_deadline_terminalizes(_db()))
    print(checks_dispatch.check_credit_job_terminalization_releases_credit(_db()))


def test_outbox__pending_redelivery_superseded_and_snapshot() -> None:
    """DUR-OUTBOX: at-least-once rows, superseded acks, the rebuild snapshot."""
    from . import checks_dispatch
    print(checks_dispatch.check_dispatch_relay(_db()))


# --- item 3: outbox expiry and GC ---------------------------------------------------
def test_outbox__expiry_and_gc_never_delete_what_a_consumer_needs() -> None:
    """DUR-OUTBOX: terminal dispatch rows expire; old acknowledged rows are deleted,
    bounded, never a live job's, an unacknowledged or a callback delivery's."""
    from . import checks_dispatch
    print(checks_dispatch.check_outbox_gc(_db()))


# --- item 4: W2's result object and prompt count, M3's liveness -----------------------
def test_results__write_once_owner_read_and_the_prepared_prompt_count() -> None:
    """W2 requests: put_result / read_result, prepared prompt_tokens within the ceiling."""
    from . import checks_dispatch
    print(checks_dispatch.check_results_and_prompt_tokens(_db()))
