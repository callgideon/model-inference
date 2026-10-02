"""W1 (api-traces): the grantor's data-use routes through the composition root (`create_app`).

    uv run --frozen pytest -q tests/ap07/test_composed.py

Off (the default): no data-use route exists and `GET /console/v1/data-use` is a 404. On, with
the session actors (AP-01's `SessionActors`; `control.StaticActors` here) and a stub service:
200 no-store. On without the session actors: a refusal to start.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

from infrx.config import RuntimeMisconfigured
from infrx.console import data_use
from infrx.contracts import api
from infrx.gateway import app as composition
from infrx.gateway import control
from infrx.scheduling.memory import MemoryScheduler

from tests.g import relay_support, support

OWNER = api.Actor(audience="session", user_id="2b2b2b2b-0000-4000-8000-000000000002",
                  org_id="1a1a1a1a-0000-4000-8000-000000000001")


class Stub:
    def __init__(self) -> None:
        self.calls: list[api.Actor] = []

    async def read(self, actor: api.Actor) -> data_use.DataUseDoc:
        self.calls.append(actor)
        return data_use.DataUseDoc(consent=data_use.NO_CONSENT, keys=(), grants=())


def composed(**overrides):
    world = relay_support.World()
    world.stream.usage = lambda: asyncio.sleep(0, {})
    on = {k: overrides.pop(k) for k in ("console_data_use",) if k in overrides}
    config = support.settings(deployment=dataclasses.replace(support.BUILD, **on))
    return composition.create_app(config, client=support.upstream(), sb=support.supabase(),
                                  clock=world.now_s, catalog=world.catalog, stream=world.stream,
                                  objects=world.objects, jobs=world.jobs,
                                  index=MemoryScheduler(world.clock.now), **overrides)


def test_composed__off_by_default_no_data_use_route_exists():
    app = composed(data_use=Stub())
    assert app.state.runtime.data_use is None
    assert TestClient(app).get("/console/v1/data-use").status_code == 404


def test_composed__on_without_the_session_actors_refuses_to_start():
    with pytest.raises(RuntimeMisconfigured) as refused:
        composed(console_data_use=True, data_use=Stub())
    assert refused.value.missing == ("SESSION_ACTORS",)


def test_composed__on_a_session_reads_its_data_use_200_no_store(monkeypatch):
    monkeypatch.setattr(composition.Runtime, "actors", control.StaticActors(OWNER),
                        raising=False)
    service = Stub()
    app = composed(console_data_use=True, data_use=service)
    r = TestClient(app).get("/console/v1/data-use")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    assert service.calls == [OWNER]
