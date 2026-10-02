"""The two AP-01 identity worlds (`worlds.py`). `world` runs a case in memory and, marked `pg`,
on PostgreSQL (`INFRX_D_TASK=ap1`): one seeded template per module, a fresh copy per case (the
stores commit on their own connections). The `pg` half skips visibly without Docker."""
from __future__ import annotations

import pytest

from tests.ap01 import worlds
from tests.d import pgharness

TEMPLATE, CASE = f"{pgharness.DATABASE}_ap01tpl", f"{pgharness.DATABASE}_ap01case"


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
        worlds.seed_pg(conn)
    return TEMPLATE


@pytest.fixture
def pg_world(pg_template):
    pgharness.assert_ours("copy a database in")
    with pgharness.connect("postgres") as admin:
        admin.execute(f'drop database if exists "{CASE}" with (force)')
        admin.execute(f'create database "{CASE}" template "{pg_template}"')
    with pgharness.connect(CASE) as conn:
        yield worlds.PgWorld(conn, pgharness.dsn(CASE))


@pytest.fixture
def fake_world():
    return worlds.FakeWorld()


@pytest.fixture(params=["fake", pytest.param("pg", marks=pytest.mark.pg)])
def world(request):
    return request.getfixturevalue(f"{request.param}_world")
