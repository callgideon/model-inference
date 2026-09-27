"""J2's two permission worlds (`world`: the fake of lab-sql's seam, and marked `pg`) - the PostgreSQL half: the L2 permission check on `PgAccessStore` (INFRX_D_TASK=j2, 57511),
the LAB-ACCESS world of `tests/l/access` on its own template. Skips visibly without Docker."""
from __future__ import annotations

import pytest

from tests.d import pgharness
from tests.l.access import worlds

TEMPLATE, CASE = f"{pgharness.DATABASE}_j2tpl", f"{pgharness.DATABASE}_j2case"


def pytest_configure(config):
    config.addinivalue_line("markers", "pg: runs on the lane's task-local PostgreSQL")


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
        worlds.seed_pg(conn, pgharness.dsn(TEMPLATE))
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
