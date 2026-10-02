"""AP-02 on the ap2 task-local PostgreSQL: one seeded template per process (`world.seed`), a fresh
copy per case. Skips visibly off the ap2 key or without Docker (never the d1 default)."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import api
from infrx.gateway import control
from infrx.state.jobstore import connector

from tests.d import pgharness

from . import world

SECRET = b"ap02-console-cursor-secret"
TEMPLATE, CASE = f"{pgharness.DATABASE}_ap02tpl", f"{pgharness.DATABASE}_ap02case"
_seeded: list[world.Seeded] = []


def pg_unavailable() -> str | None:
    if os.environ.get("INFRX_D_TASK") != "ap2":
        return "PostgreSQL only on the ap2 task-local key (INFRX_D_TASK=ap2)"
    return pgharness.unavailable()


@pytest.fixture(scope="session")
def seeded() -> world.Seeded:
    reason = pg_unavailable()
    if reason:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    if not _seeded:
        from infrx.state import migrations
        pgharness.ensure()
        pgharness.recreate(TEMPLATE)
        pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        with pgharness.connect(TEMPLATE) as conn:
            _seeded.append(world.seed(conn))
    return _seeded[0]


@pytest.fixture
def db(seeded):
    """(dsn, an autocommit superuser connection) on a fresh copy of the template."""
    pgharness.assert_ours("copy a database in")
    from psycopg import sql
    with pgharness.connect("postgres") as admin:
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(
            sql.Identifier(CASE)))
        admin.execute(sql.SQL("create database {} template {}").format(
            sql.Identifier(CASE), sql.Identifier(TEMPLATE)))
    with pgharness.connect(CASE) as conn:
        yield pgharness.dsn(CASE), conn


def session(user: str, org: str | None = None, *, operator: bool = False) -> api.Actor:
    return api.Actor(audience="session", user_id=user, org_id=org, operator=operator)


def client(dsn: str, actor: api.Actor | None, *, reads=None) -> TestClient:
    """The lane's router on a bare app (rule 3: mounted at merge), as the composition will."""
    from infrx.console.reads import ConsoleReads
    from infrx.gateway.routes import console_reads
    app = FastAPI()
    rt = SimpleNamespace(actors=control.StaticActors(actor),
                         console_reads=reads or ConsoleReads(connector(dsn), SECRET))
    console_reads.register(app, rt)
    return TestClient(app, raise_server_exceptions=False)
