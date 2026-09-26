#!/usr/bin/env python3
"""D10-0025 (U3 WR-U3-1, R143) on real PostgreSQL (both images): the operator console's
audited RPCs and operator-only reads, their grants, a concurrent race over 8 connections,
the re-run, and the immutability of 0001-0024.

    INFRX_D_TASK=revoke uv run --frozen pytest -q tests/d/test_operator_d10.py
    INFRX_D_TASK=revoke INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_operator_d10.py
"""
from __future__ import annotations

import subprocess

import pytest
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_operator as co
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_operator"
OPERATOR = "0025_operator_console.sql"
#: The committed base D10-0025 started from (codex/d10-app-sql: 0001-0024 final).
BASE = "8f453b98"
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


def test_only_an_operator_changes_anything_and_the_actor_is_the_jwt() -> None:
    print(co.check_operator_authority(_db()))


def test_a_key_replays_its_change_and_conflicts_on_another() -> None:
    print(co.check_operator_idempotency(_db()))


def test_an_adjustment_is_exact_nonzero_and_never_below_zero() -> None:
    print(co.check_operator_adjust(_db()))


def test_suspension_and_restore_are_audited_and_replayable() -> None:
    print(co.check_operator_suspension(_db()))


def test_a_consumer_key_is_revoked_once() -> None:
    print(co.check_operator_revoke(_db()))


def test_the_operator_reads_are_operator_only() -> None:
    print(co.check_operator_views(_db()))


def test_operator_privileges() -> None:
    print(co.check_operator_privileges(_db()))


def test_0025_is_re_runnable() -> None:
    """Applied a second time on the migrated, seeded database: same definitions and ACLs,
    and the checks still hold."""
    conn = _db()
    snap = ("select p.oid::regprocedure::text, md5(pg_get_functiondef(p.oid)), "
            "coalesce(p.proacl::text, '') from pg_proc p where p.oid = any(%s::regprocedure[]) "
            "union all select c.oid::regclass::text, md5(pg_get_viewdef(c.oid)), "
            "coalesce(c.relacl::text, '') || coalesce(array_to_string(c.reloptions, ','), '') "
            "from pg_class c where c.oid = any(%s::regclass[]) order by 1")
    params = (list(co.RPCS + (co.HELPER,)), list(co.VIEWS))
    before = conn.execute(snap, params).fetchall()
    assert len(before) == 6, before
    pgharness.apply(DB, tuple(f for f in migrations.sql_for(shim=pgharness.NEEDS_SHIM)
                              if f[0] == OPERATOR))
    assert conn.execute(snap, params).fetchall() == before, "0025 re-applied changed an object"
    print(co.check_operator_privileges(conn), co.check_operator_authority(conn))


def test_0001_to_0024_are_byte_identical_to_the_base() -> None:
    root = subprocess.run(("git", "rev-parse", "--show-toplevel"), capture_output=True,
                          text=True, cwd=migrations.DIR)
    if root.returncode != 0 or subprocess.run(
            ("git", "cat-file", "-e", f"{BASE}^{{commit}}"), cwd=migrations.DIR).returncode:
        pytest.skip(f"no git checkout with {BASE}")
    frozen = sorted(p.name for p in migrations.DIR.glob("00*.sql") if p.name < "0025_")
    assert len(frozen) == 24, frozen
    diff = subprocess.run(("git", "diff", "--name-only", BASE, "--", *frozen),
                          capture_output=True, text=True, cwd=migrations.DIR)
    assert diff.returncode == 0 and diff.stdout == "", diff.stdout or diff.stderr


def test_concurrent_console_changes_apply_once() -> None:
    """Last on purpose: it commits its changes into this module's database."""
    _db()
    print(co.check_operator_races(pgharness.connect, DB))
