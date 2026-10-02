#!/usr/bin/env python3
"""AP-00 slice 00d: `0060_control_operations.sql` on real PostgreSQL through `PgControlOps` -
the protocol's scenarios (`test_control_ops_units.py`, the same ones the fake passes), the
privileges, and two API instances racing one key. Each `check_*` is the check a mutant in
`test_control_ops_mutants.py` must break. Plain image and `INFRX_D1_IMAGE=supabase`.

    INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_control_ops.py
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import psycopg
import pytest
from infrx.state import migrations
from infrx.state.control_ops import PgControlOps, Started
from infrx.state.jobstore import connector
from psycopg.types.json import Jsonb

from . import pgharness
from . import test_control_ops_units as u

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_control_ops"
BOUNDARY = tuple(f"infrx.control_op_{m}(jsonb)" for m in
                 ("start", "lease", "advance", "finish", "cancel", "get", "pending"))
INTERNAL = ("infrx.control_owner(jsonb)", "infrx.control_op_row(jsonb)",
            "infrx.control_op_doc(infrx.control_operations)")
WRITERS = ("service_role", "infrx_lab_control")
#: no 0060 function: the gateway's dedicated login (its set is checks_reads.RUNTIME_FUNCTIONS)
OTHERS = ("anon", "authenticated", "infrx_runtime", "infrx_monitor")
TABLES = ("infrx.control_operations", "infrx.control_idempotency")


def seed(conn) -> None:
    """Nothing: an operation names its tenant in its actor, so no row precedes it."""


def pg_env(conn) -> SimpleNamespace:
    async def advance(seconds: float) -> None:
        conn.execute("select infrx_test.advance(%s)", (seconds,))
    return SimpleNamespace(ops=PgControlOps(connector(pgharness.dsn(conn.info.dbname))),
                           advance=advance)


def _on_pg(scenario):
    def check(conn) -> str:
        return u.run(scenario, pg_env(conn))
    check.__name__ = f"check_{scenario.__name__}"
    return check


def _can(conn, role: str, what: str, obj: str, privilege: str) -> bool:
    return conn.execute(f"select has_{what}_privilege(%s, %s, %s)",
                        (role, obj, privilege)).fetchone()[0]


def check_browser_roles_reach_nothing(conn) -> str:
    """R271 / DUR-RLS: no browser session (nor the gateway's pinned dedicated logins) reads a
    table or executes a function of 0060; the writer logins execute exactly the seven boundary
    functions and write nothing directly; the helpers are nobody's; row security is on."""
    reached = [f"{r} {f}" for r in OTHERS for f in BOUNDARY + INTERNAL
               if _can(conn, r, "function", f, "execute")]
    reached += [f"{r} {p} {t}" for r in OTHERS for t in TABLES
                for p in ("select", "insert", "update", "delete")
                if _can(conn, r, "table", t, p)]
    assert not reached, f"browser roles reach 0060: {reached}"
    missing = [f"{r} {f}" for r in WRITERS for f in BOUNDARY
               if not _can(conn, r, "function", f, "execute")]
    assert not missing, f"a runtime login cannot run an operation: {missing}"
    helpers = [f"{r} {f}" for r in WRITERS for f in INTERNAL
               if _can(conn, r, "function", f, "execute")]
    assert not helpers, f"an internal helper is callable: {helpers}"
    writes = [f"{r} {p} {t}" for r in WRITERS for t in TABLES
              for p in ("insert", "update", "delete") if _can(conn, r, "table", t, p)]
    assert not writes, f"a login writes around the functions: {writes}"
    assert _can(conn, "service_role", "table", TABLES[0], "select")
    rls = dict(conn.execute("select oid::regclass::text, relrowsecurity from pg_class "
                            "where oid = any(%s::regclass[])", (list(TABLES),)).fetchall())
    assert rls == {t: True for t in TABLES}, rls
    return (f"{len(OTHERS)} other roles reach nothing; {len(WRITERS)} logins x "
            f"{len(BOUNDARY)} functions; RLS on")


def check_two_instances_race_one_key_to_one_operation(conn) -> str:
    """API-ACTIONS-style race: eight concurrent starts of one key on separate connections
    make one operation; every other start is its replay."""
    ops = pg_env(conn).ops
    who, k = u.actor(provider=u.uid(), user=u.uid()), u.kind()

    async def race():
        return await asyncio.gather(*(ops.start(k, who, "raced", u.H1) for _ in range(8)),
                                    return_exceptions=True)
    started = asyncio.run(race())
    failed = [repr(s) for s in started if isinstance(s, BaseException)]
    assert not failed, f"a racing start failed: {failed[:2]}"
    started = [s for s in started if isinstance(s, Started)]
    ids = {s.operation.operation_id for s in started}
    assert len(ids) == 1 and sum(not s.replayed for s in started) == 1, \
        [(s.operation.operation_id, s.replayed) for s in started]
    count = conn.execute("select count(*) from infrx.control_operations where kind = %s",
                         (k,)).fetchone()[0]
    assert count == 1, f"{count} operations for one key"
    return "8 racing starts: 1 operation, 7 replays"


def check_the_sql_refuses_an_actor_python_never_sends(conn) -> str:
    """0060 checks the actor's audience itself (R270's four), so a caller that bypasses the
    pydantic `Actor` cannot store an operation no reader can classify."""
    body = {"kind": "artifact.verify", "idempotency_key": "raw", "input_hash": u.H1,
            "retention_s": 86400, "actor": {"audience": "root", "provider_org_id": u.uid()}}
    with conn.transaction(force_rollback=True):      # the same body, a known audience: stored
        conn.execute("select infrx.control_op_start(%s)",
                     (Jsonb({**body, "actor": {**body["actor"], "audience": "session"}}),))
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("select infrx.control_op_start(%s)", (Jsonb(body),))
    except psycopg.errors.RaiseException as refused:
        assert str(refused).startswith("invalid_request:"), refused
        return "an unknown audience is invalid_request"
    raise AssertionError("an operation with audience 'root' was stored")


CHECKS = {c.__name__: c for c in (
    *(_on_pg(s) for s in u.SCENARIOS),
    check_browser_roles_reach_nothing,
    check_two_instances_race_one_key_to_one_operation,
    check_the_sql_refuses_an_actor_python_never_sends)}


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_control_ops(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
