"""AP-06's two worlds: L3's fake (`fake_world`) and, on the ap6 key only (57558, never d1),
L3's PostgreSQL world (`pg_world`: the whole local migration chain, 0060's receipts included,
one seeded template per module, a fresh copy per case)."""
from __future__ import annotations

import os

import pytest

from tests.d import pgharness
from tests.l.control import worlds

KEY = "ap6"
TEMPLATE, CASE = f"{pgharness.DATABASE}_ap6tpl", f"{pgharness.DATABASE}_ap6case"


def pg_unavailable() -> str | None:
    if os.environ.get("INFRX_D_TASK") != KEY:
        return f"PostgreSQL only on the {KEY} task-local key (INFRX_D_TASK={KEY})"
    reason = pgharness.unavailable()
    return None if reason is None else f"task-local PostgreSQL unavailable: {reason}"


@pytest.fixture
def fake_world():
    return worlds.FakeWorld()


@pytest.fixture(scope="module")
def pg_template():
    reason = pg_unavailable()
    if reason:
        pytest.skip(reason)
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
        w = worlds.PgWorld(conn, pgharness.dsn(CASE))
        w.dsn = pgharness.dsn(CASE)
        yield w
