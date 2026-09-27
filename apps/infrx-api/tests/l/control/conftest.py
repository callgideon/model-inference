"""The two LAB-PUBLISH worlds (`worlds.py`). `world` runs a case on the fake and, marked `pg`, on
PostgreSQL: one seeded template per module, a fresh copy per case. The `pg` half skips visibly
without Docker or before L3-SQL merges (`infrx.state.lab_control`, `PgControlStore`)."""
from __future__ import annotations

import pytest

from tests.d import pgharness

from . import worlds


def pytest_configure(config):
    config.addinivalue_line("markers", "pg: runs on the lane's task-local PostgreSQL")


TEMPLATE, CASE = f"{pgharness.DATABASE}_l3tpl", f"{pgharness.DATABASE}_l3case"


@pytest.fixture(scope="module")
def pg_template():
    try:
        import infrx.state.lab_control  # noqa: F401
    except ImportError:
        pytest.skip("L3-SQL is not merged: infrx.state.lab_control (PgControlStore) is absent")
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
