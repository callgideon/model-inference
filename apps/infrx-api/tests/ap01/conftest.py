"""The AP-01 identity worlds (`worlds.py`). `world` runs a case in memory and, marked `pg`,
on PostgreSQL (`INFRX_D_TASK=ap1`) through both PostgreSQL stores (`pg`: 0065's functions,
`pg-direct`: the fallback): one seeded template per module, a fresh copy per case (the stores
commit on their own connections). The `pg` half skips visibly without Docker. Until 0065 is on
the base the template gets its functions from `identity_0065.sql` (SR-AP01-1's DDL)."""
from __future__ import annotations

import pathlib

import pytest

from tests.ap01 import worlds
from tests.d import pgharness

TEMPLATE, CASE = f"{pgharness.DATABASE}_ap01tpl", f"{pgharness.DATABASE}_ap01case"
FUNCTIONS = pathlib.Path(__file__).with_name("identity_0065.sql")
LAB_PASSWORD = "infrx-ap1-lab-control"


@pytest.fixture(scope="module")
def pg_template():
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(TEMPLATE)
    pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(TEMPLATE) as conn:
        if conn.execute("select to_regprocedure('infrx.identity_account(uuid)')").fetchone()[0] \
                is None:                       # ponytail: until 0065 merges
            conn.execute(FUNCTIONS.read_text())
        worlds.seed_pg(conn)
        conn.execute(f"alter role infrx_lab_control password '{LAB_PASSWORD}'")
    return TEMPLATE


def _copy(pg_template: str) -> None:
    pgharness.assert_ours("copy a database in")
    with pgharness.connect("postgres") as admin:
        admin.execute(f'drop database if exists "{CASE}" with (force)')
        admin.execute(f'create database "{CASE}" template "{pg_template}"')


@pytest.fixture
def pg_world(pg_template):
    _copy(pg_template)
    with pgharness.connect(CASE) as conn:
        yield worlds.PgWorld(conn, pgharness.dsn(CASE))


@pytest.fixture
def pg_direct_world(pg_template):
    _copy(pg_template)
    with pgharness.connect(CASE) as conn:
        yield worlds.PgWorld(conn, pgharness.dsn(CASE), "pg-direct")


@pytest.fixture
def fake_world():
    return worlds.FakeWorld()


@pytest.fixture(params=["fake", pytest.param("pg", marks=pytest.mark.pg),
                        pytest.param("pg_direct", marks=pytest.mark.pg)])
def world(request):
    return request.getfixturevalue(f"{request.param}_world")
