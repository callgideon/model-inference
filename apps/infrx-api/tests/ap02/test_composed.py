"""WR-AP02-1: the console reads through the composition root (`create_app`), not a bare app.

    INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02/test_composed.py

Off (the default): no /console or /operator route exists and the path is a 404. On, with the
session actors (AP-01's `SessionActors`; a stub here), the DSN and a 16-byte cursor key: a seeded
session reads its credits 200 no-store on ap2. On without one of them: a refusal to start that
names the setting, never its value.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured
from infrx.gateway import app as composition
from infrx.gateway import control
from infrx.scheduling.memory import MemoryScheduler

from tests.g import relay_support, support

from .conftest import session

SECRET = "ap02-composed-cursor-secret"


def composed(**on):
    world = relay_support.World()
    world.stream.usage = lambda: asyncio.sleep(0, {})
    config = support.settings(deployment=dataclasses.replace(support.BUILD, **on))
    return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                  clock=world.now_s, catalog=world.catalog, stream=world.stream,
                                  objects=world.objects, jobs=world.jobs,
                                  index=MemoryScheduler(world.clock.now))


def console_paths(app):
    return [r for r in app.routes
            if getattr(r, "path", "").startswith(("/console/", "/operator/"))]


def test_composed__off_by_default_no_console_route_exists():
    app = composed()
    assert app.state.runtime.console_reads is None and console_paths(app) == []
    assert TestClient(app).get("/console/v1/credits").status_code == 404


@pytest.mark.parametrize("on", [
    {"console_database_url": "", "console_cursor_secret": SECRET},
    {"console_database_url": "postgresql://reads@127.0.0.1:1/x", "console_cursor_secret": ""},
], ids=["no-dsn", "no-secret"])
def test_composed__on_without_the_dsn_or_the_key_refuses_to_start(on, monkeypatch):
    monkeypatch.setattr(composition.Runtime, "actors", control.StaticActors(), raising=False)
    with pytest.raises(RuntimeMisconfigured) as refused:
        composed(console_reads=True, **on)
    assert {"CONSOLE_DATABASE_URL", "CONSOLE_CURSOR_SECRET"} <= set(refused.value.missing)
    assert "127.0.0.1" not in str(refused.value) and SECRET not in str(refused.value)


def test_composed__a_short_key_refuses_to_start(monkeypatch):
    monkeypatch.setattr(composition.Runtime, "actors", control.StaticActors(), raising=False)
    with pytest.raises(RuntimeMisconfigured, match="CONSOLE_CURSOR_SECRET"):
        composed(console_reads=True, console_database_url="postgresql://reads@127.0.0.1:1/x",
                 console_cursor_secret="short")


def test_composed__on_without_the_session_actors_refuses_to_start():
    with pytest.raises(RuntimeMisconfigured) as refused:
        composed(console_reads=True, console_database_url="postgresql://reads@127.0.0.1:1/x",
                 console_cursor_secret=SECRET)
    assert refused.value.missing == ("SESSION_ACTORS",)


@pytest.mark.pg
def test_composed__on_a_seeded_session_reads_its_credits(db, seeded, monkeypatch):
    dsn, _ = db
    monkeypatch.setattr(composition.Runtime, "actors",
                        control.StaticActors(session(seeded.me, seeded.my_org)), raising=False)
    app = composed(console_reads=True, console_database_url=dsn, console_cursor_secret=SECRET)
    assert len(console_paths(app)) == 12
    r = TestClient(app).get("/console/v1/credits")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    assert r.json()["available"]["unit"] == "CREDIT"
