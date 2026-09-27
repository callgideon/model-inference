#!/usr/bin/env python3
"""LW1 integration (R156, WR-L1-5/WR-L1-7): the Lab App's session door to THE membership read,
`public.lab_provider_memberships()` (0030), on real PostgreSQL.

The Lab shell (`apps/lab/lib/auth/memberships.ts`) calls it on the signed-in user's own
session: no argument, so the identity is `auth.uid()`'s. It must answer exactly what the L2 port
(`LabAccess.workspaces` over `PgAccessStore`) answers for that user, on the database clock:
the same (provider, name, role) set, current memberships only, and nothing else leaves the
database. Role matrix: lab-sql's L2-SQL world (two consumers, two providers, BOTH in both
products, a NEMO viewer, the operator) plus a membership revoked earlier, one not yet granted
and one revoked during the run.

    INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_l2sql_self.py
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from infrx.lab.access import LabAccess
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore

from . import checks
from . import checks_credit as cc
from . import pgharness
from . import test_l2sql_access as t

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_l2self"

NEMO, OTHER, NAME = t.NEMO, t.OTHER, "NemoStation"      # the seed's two providers share a name
EARLIER = "5e000000-0000-4000-8000-000000000001"         # revoked a day ago
LATER = "5e000000-0000-4000-8000-000000000002"           # granted tomorrow
LEAVING = "5e000000-0000-4000-8000-000000000003"         # revoked in an hour (during the run)
READ = "select public.lab_provider_memberships()"
KEYS = {"provider_org_id", "provider_name", "role"}      # memberships.ts reads these, no more

EXPECTED = {
    t.DEV: {(NEMO, NAME, "developer")},
    t.VIEWER: {(NEMO, NAME, "viewer")},
    t.BOTH: {(OTHER, NAME, "developer")},                # dual user: the provider half only
    t.C1: set(),                                         # consumer owner only
    checks.USER_OPERATOR: set(),                         # the operator bit is no membership
    EARLIER: set(), LATER: set(),
    LEAVING: {(OTHER, NAME, "viewer")},
}


def seed(conn) -> None:
    """lab-sql's L2-SQL world, plus the three membership edges on the database clock."""
    t.seed(conn)
    conn.execute("insert into auth.users (id, email) values (%s, 'earlier@example.com'), "
                 "(%s, 'later@example.com'), (%s, 'leaving@example.com')",
                 (EARLIER, LATER, LEAVING))
    conn.execute(
        "insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by, "
        "granted_at, revoked_at) values "
        "(%s, %s, 'developer', 'ops', infrx.now() - interval '2 days', "
        " infrx.now() - interval '1 day'), "
        "(%s, %s, 'developer', 'ops', infrx.now() + interval '1 day', null), "
        "(%s, %s, 'viewer', 'ops', infrx.now() - interval '1 day', "
        " infrx.now() + interval '1 hour')",
        (NEMO, EARLIER, NEMO, LATER, OTHER, LEAVING))


def session_rows(conn, user: str) -> list[dict]:
    """The RPC's answer on `user`'s browser session (rolled back); a refusal is an assertion."""
    try:
        with conn.transaction():
            conn.execute(checks._jwt(user))
            (rows,) = conn.execute(READ).fetchone()
            raise psycopg.Rollback()
    except psycopg.Error as refused:
        raise AssertionError(f"{user}'s session was refused: {refused.sqlstate}") from None
    return rows


def as_set(rows) -> set[tuple[str, str, str]]:
    return {(r["provider_org_id"], r["provider_name"], r["role"]) for r in rows}


def port_workspaces(dsn: str, user: str) -> set[tuple[str, str, str]]:
    """L2's answer for the same user (service role, the Python predicate, the store clock)."""
    found = asyncio.run(LabAccess(PgAccessStore(connector(dsn))).workspaces(user))
    return {(w.membership.provider_org_id, w.provider_name, w.membership.role.value)
            for w in found}


# ----------------------------------------------------------------------------- checks
def check_the_session_reads_its_own_current_workspaces_as_the_port_does(conn, dsn) -> str:
    """R156: one answer. Each session gets exactly its current memberships, the same set L2's
    port gives, with the three columns the Lab reads and nothing else (no user, granting
    operator or dates)."""
    for user, expected in EXPECTED.items():
        rows = session_rows(conn, user)
        assert all(set(r) == KEYS for r in rows), f"{user}: columns {[sorted(r) for r in rows]}"
        assert len(rows) == len(as_set(rows)), f"{user}: duplicate rows {rows}"
        assert as_set(rows) == expected, f"{user}: session {as_set(rows)} != {expected}"
        port = port_workspaces(dsn, user)
        assert port == expected, f"{user}: the L2 port {port} != {expected}"
    return f"{len(EXPECTED)} users: session == port == expected"


def check_only_a_signed_in_session_calls_it(conn, dsn) -> str:
    """The browser door: anon is refused (42501); `authenticated` is the only browser grantee
    (the platform role holds every function by 0004's default and reads `[]`: no auth.uid());
    the function runs as its owner, since schema `infrx` is closed to browsers."""
    got = cc.refused_as(conn, "anon", READ)
    assert got is not None and got.startswith("42501"), f"anon: {got or 'allowed'}"
    got = cc.refused_as(conn, "consumer", READ)
    assert got is None, f"a signed-in session is refused: {got}"
    acl = conn.execute("select coalesce(proacl::text, '') from pg_proc where oid = "
                       "'public.lab_provider_memberships()'::regprocedure").fetchone()[0]
    grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
    assert grantees == {"postgres", "authenticated", "service_role"}, f"grantees: {acl}"
    return "anon 42501; authenticated only"


def check_a_revocation_ends_the_workspace_at_its_instant(conn, dsn) -> str:
    """WR-L1-7: revoked during the run, gone from both reads at `revoked_at` itself (the
    contract's `now < revoked_at`), on the database clock. Moves the clock: runs last."""
    assert as_set(session_rows(conn, LEAVING)) == {(OTHER, NAME, "viewer")}
    conn.execute("select infrx_test.advance(3600)")
    assert session_rows(conn, LEAVING) == [], "a revoked membership is still a workspace"
    assert port_workspaces(dsn, LEAVING) == set(), "the L2 port disagrees"
    assert as_set(session_rows(conn, t.DEV)) == EXPECTED[t.DEV]
    return "LEAVING listed before, gone at revoked_at from session and port"


CHECKS = {c.__name__: c for c in (
    check_the_session_reads_its_own_current_workspaces_as_the_port_does,
    check_only_a_signed_in_session_calls_it,
    check_a_revocation_ends_the_workspace_at_its_instant)}


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection, pgharness.dsn(DB)


@pytest.mark.parametrize("name", list(CHECKS))
def test_l2sql_self(world, name) -> None:
    print(f"{name}: {CHECKS[name](*world)}")
