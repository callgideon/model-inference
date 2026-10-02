"""WR-AP03-3: the console/operator mutations through the composition root (`create_app`).

    uv run --frozen pytest -q tests/ap03/test_composed.py

Off (the default): no mutation route exists and `POST /console/v1/keys` is a 404. On, with
the session actors (AP-01's `SessionActors`; `control.StaticActors` here) and a recording
repository: 201 no-store. On without the session actors: a refusal to start.
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

from .test_routes import CONSUMER, IDEM, Recording


def composed(**overrides):
    world = relay_support.World()
    world.stream.ready = lambda: asyncio.sleep(0, True)
    on = {k: overrides.pop(k) for k in ("console_actions_api",) if k in overrides}
    config = support.settings(deployment=dataclasses.replace(support.BUILD, **on))
    return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                  clock=world.now_s, catalog=world.catalog, stream=world.stream,
                                  objects=world.objects, jobs=world.jobs,
                                  index=MemoryScheduler(world.clock.now), **overrides)


def test_composed__off_by_default_no_mutation_route_exists():
    app = composed(console_actions=Recording())
    assert app.state.runtime.console_actions is None
    assert TestClient(app).post("/console/v1/keys", json={"name": "laptop"},
                                headers=IDEM).status_code == 404


def test_composed__on_without_the_session_actors_refuses_to_start():
    with pytest.raises(RuntimeMisconfigured) as refused:
        composed(console_actions_api=True, console_actions=Recording())
    assert refused.value.missing == ("SESSION_ACTORS",)


def test_composed__on_a_session_creates_a_key_201_no_store(monkeypatch):
    monkeypatch.setattr(composition.Runtime, "actors", control.StaticActors(CONSUMER),
                        raising=False)
    repo = Recording()
    app = composed(console_actions_api=True, console_actions=repo)
    r = TestClient(app).post("/console/v1/keys", json={"name": "laptop"}, headers=IDEM)
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store", r.text
    assert [c[0] for c in repo.calls] == ["create_key"]
