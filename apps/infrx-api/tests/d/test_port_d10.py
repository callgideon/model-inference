#!/usr/bin/env python3
"""D10-APP-SQL (0024) on real PostgreSQL (both images): C0's paginated consumer CREDIT ledger,
U1R's credits-in partial index and consumer_jobs filters, U4's withheld result, C3A's
verified-individual key insert, W5-F5's monitor read of unknown holds, their grants, the
re-run, and the immutability of 0001-0023.

    INFRX_D_TASK=revoke uv run --frozen pytest -q tests/d/test_port_d10.py
    INFRX_D_TASK=revoke INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_port_d10.py
"""
from __future__ import annotations

import subprocess

import pytest
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_port as cp
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_port"
PORT = "0024_console_read_port.sql"
#: The committed base D10-APP-SQL started from (codex/door-revoke, 0001-0023 final).
BASE = "273990a0"
_state: dict = {}


def _db():
    if "conn" not in _state:
        pgharness.ensure()
        pgharness.recreate(DB)
        pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(DB)
        ca.seed_admission(conn)
        _state["conn"] = conn
    return _state["conn"]


def test_the_consumer_credit_ledger_is_own_rows_exact_and_bounded() -> None:
    print(cp.check_consumer_credit_ledger(_db()))


@pytest.mark.skipif(pgharness.ON_SUPABASE, reason="auto_explain needs a superuser LOAD; "
                    "Supabase's `postgres` is not one (measured): the plan is proven on the "
                    "plain image, the function and index are the same SQL on both")
def test_a_ledger_page_is_a_limit_bounded_index_range() -> None:
    print(cp.check_ledger_page_plan(_db()))


def test_credits_in_reads_the_partial_index() -> None:
    print(cp.check_credits_in_index(_db()))


def test_consumer_jobs_filters_narrow_and_default_to_the_old_read() -> None:
    print(cp.check_consumer_jobs_filters(_db()))


def test_port_privileges() -> None:
    print(cp.check_port_privileges(_db()))


def test_the_owners_result_is_withheld_while_usage_is_unknown() -> None:
    print(cp.check_result_withheld(_db()))


def test_a_browser_key_insert_needs_a_verified_individual() -> None:
    print(cp.check_key_insert_needs_verified_individual(_db()))


def test_0024_is_re_runnable() -> None:
    """Applied a second time on the migrated, seeded database: same definitions, grants and
    index, and every check above still holds."""
    conn = _db()
    snap = ("select p.oid::regprocedure::text, md5(pg_get_functiondef(p.oid)), "
            "coalesce(p.proacl::text, '') from pg_proc p join pg_namespace n on "
            "n.oid = p.pronamespace where n.nspname = 'public' and p.proname in "
            "('consumer_jobs', 'consumer_credit_ledger', 'consumer_job_result', "
            "'consumer_may_create_key') union all select indexname, md5(indexdef), '' "
            "from pg_indexes where indexname = 'credit_ledger_wallet_credits_in_idx' "
            "union all select polname, md5(pg_get_expr(polwithcheck, polrelid)), '' from "
            "pg_policy where polname = 'api_keys_insert_owner' union all select "
            "'monitor_reads', md5(coalesce(pg_get_expr(polqual, polrelid), '')), "
            "polroles::regrole[]::text from pg_policy where polname = 'monitor_reads' and "
            "polrelid = 'infrx.credit_wallet_holds'::regclass union all select "
            "'credit_wallet_holds.state', coalesce(attacl::text, ''), '' from pg_attribute "
            "where attrelid = 'infrx.credit_wallet_holds'::regclass and attname = 'state' "
            "order by 1")
    before = conn.execute(snap).fetchall()
    assert len(before) == 8 and any("infrx_monitor=" in r[1] for r in before), before
    pgharness.apply(DB, tuple(f for f in migrations.sql_for(shim=pgharness.NEEDS_SHIM)
                              if f[0] == PORT))
    assert conn.execute(snap).fetchall() == before, "0024 re-applied changed an object"
    print(cp.check_port_privileges(conn), cp.check_consumer_credit_ledger(conn),
          cp.check_result_withheld(conn), cp.check_key_insert_needs_verified_individual(conn))


def test_0001_to_0023_are_byte_identical_to_the_base() -> None:
    root = subprocess.run(("git", "rev-parse", "--show-toplevel"), capture_output=True,
                          text=True, cwd=migrations.DIR)
    if root.returncode != 0 or subprocess.run(
            ("git", "cat-file", "-e", f"{BASE}^{{commit}}"), cwd=migrations.DIR).returncode:
        pytest.skip(f"no git checkout with {BASE}")
    frozen = sorted(p.name for p in migrations.DIR.glob("00*.sql") if p.name < "0024_")
    assert len(frozen) == 23, frozen
    diff = subprocess.run(("git", "diff", "--name-only", BASE, "--", *frozen),
                          capture_output=True, text=True, cwd=migrations.DIR)
    assert diff.returncode == 0 and diff.stdout == "", diff.stdout or diff.stderr


def test_the_monitor_login_counts_credit_unknown_holds() -> None:
    """Last on purpose: it commits one held_unknown job into this module's database."""
    print(cp.check_monitor_reads_unknown_holds(_db(), DB))
