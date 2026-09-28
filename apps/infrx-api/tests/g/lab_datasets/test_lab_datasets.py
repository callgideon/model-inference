#!/usr/bin/env python3
"""WR-N4-1 (+ WR-N-3): `/lab/v1/providers/{provider}/datasets` - DATA-IMPORT and LAB-ACCESS,
the route half, as the gateway mounts it.

    uv run --frozen pytest -q tests/g/lab_datasets

N1/N2/N3 are the real modules over the N lanes' fake D7 (`tests/n/imports/world.py`, 0029's
rules in memory) and in-memory Lab objects; the membership is L2's `LabAccess` over the
LAB-ACCESS fake world; the session verifier is a minimal fake of `lab_auth.Sessions`. The Lab
journey against this router on real D7 is N4's (`apps/lab/tests/n/`).

Failure oracles: a route mounted without its switch; an upload whose provider identity is
anything but the verified Lab session (an API key, a body field, an unverified token); a body
read (or parsed) before the acting provider is derived; a viewer or another provider's
member importing; an unbounded or malformed body reaching N1 (a 500 instead of a refusal).
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.gateway.routes import lab_datasets as ld
from infrx.media.store import InMemoryObjectStore

from .. import support
from ...l.access.worlds import FakeWorld
from ...n.imports.world import GRANT_ID, FakeLabStore, fixture, grant_ref


def outcome(call):
    """What `call()` returns, or the exception it raised (compared, never a crash)."""
    try:
        return call()
    except Exception as died:              # noqa: BLE001
        return died


def token(user: str) -> str:
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    def __init__(self, users) -> None:
        self.users = {token(u): u for u in users}

    async def user_id(self, value: str) -> str:
        if value not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[value]


class Store(FakeLabStore):
    """The fake D7 recording who published."""

    def __init__(self) -> None:
        super().__init__()
        self.actors: list[str] = []

    async def register_source(self, **kw):
        self.actors.append(kw["actor"])
        return await super().register_source(**kw)


class World(FakeWorld):
    def __init__(self) -> None:
        super().__init__()
        self.sessions = Sessions((self.DEV_A, self.DEV_B, self.VIEWER_A, self.CONSUMER_ONLY))
        self.store, self.objects = Store(), InMemoryObjectStore()
        self.store.add_grant(provider=self.A)

    def datasets(self) -> ld.LabDatasets:
        return ld.LabDatasets(self.sessions, self.access, self.store, self.objects)

    def app(self) -> FastAPI:
        app, rt = FastAPI(), support.runtime()
        rt.lab_datasets = self.datasets()
        assert ld.register(app, rt) is rt.lab_datasets
        return app

    def spec(self) -> dict:
        spec, _ = fixture("benchmark")
        return {**spec, "provider_org_id": self.A, "grant_ref": grant_ref(GRANT_ID, self.A)}


def base(provider: str) -> str:
    return ld.PREFIX.format(provider=provider)


def call(client, user, method, path, body=None, *, raw=None):
    headers = {"content-type": "application/json"}
    if user:
        headers["authorization"] = f"Bearer {token(user)}"
    if raw is None and body is not None:
        import json
        raw = json.dumps(body).encode()
    return client.request(method, path, content=raw, headers=headers)


def imported(w: World, client, user):
    _, data = fixture("benchmark")
    started = call(client, user, "POST", base(w.A) + "/imports",
                   {"spec": w.spec(), "body": data.decode(), "accept_rejects": True})
    if started.status_code != 200:
        return started, None
    for _ in range(100):
        job = call(client, user, "GET", base(w.A) + f"/imports/{w.spec()['import_id']}")
        if job.json().get("state") != "running":
            return started, job
        time.sleep(0.02)
    return started, job


def test_lab_datasets__nothing_is_mounted_without_the_switch():
    """LAB_DATASETS off (no `rt.lab_datasets`): no route, a 404 - never a stand-in."""
    app = FastAPI()
    assert outcome(lambda: ld.register(app, support.runtime())) is None
    with TestClient(app, raise_server_exceptions=False) as client:
        assert call(client, None, "GET", base(FakeWorld.A) + "/versions").status_code == 404


def test_lab_datasets__the_upload_identity_is_the_verified_lab_session_only():
    """WR-N-3: no bearer, an API key, an unknown token - 401 before anything; a body field
    naming another actor changes nothing: N1 records the session's user."""
    w = World()
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        path = base(w.A) + "/imports"
        for headers in ({}, {"authorization": "Bearer sk-live-0123456789abcdef"},
                        {"authorization": f"Bearer {token('0' * 8 + '-0000-4000-8000-' + '0' * 12)}"}):
            answer = client.post(path, content=b"{}", headers={
                "content-type": "application/json", **headers})
            assert answer.status_code == 401, answer.text
        assert w.store.actors == []
        started, job = imported(w, client, w.DEV_A)
        assert started.status_code == 200 and started.json()["state"] == "running"
        assert job.json()["state"] == "published", job.text
        assert set(w.store.actors) == {w.DEV_A}
        listed = call(client, w.DEV_A, "GET", base(w.A) + "/versions")
        assert [v["dataset_ref"] for v in listed.json()] == [job.json()["report"]["dataset_ref"]]


def test_lab_datasets__the_body_is_read_only_after_the_acting_provider():
    """A malformed body from a caller who may not act for the provider is that caller's
    refusal (401/403/404), never a 400 that shows the body was parsed first."""
    w = World()
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        path = base(w.A) + "/imports"
        for user, status in ((None, 401), (w.VIEWER_A, 403), (w.DEV_B, 404),
                             (w.CONSUMER_ONLY, 404), (w.DEV_A, 400)):
            answer = call(client, user, "POST", path, raw=b"{not json")
            assert answer.status_code == status, (user, answer.text)


def test_lab_datasets__a_viewer_and_another_providers_member_cannot_import():
    """acting_provider (WR-N-2): developer+ of THIS provider; a viewer 403, a developer of
    another provider 404 (a foreign id confirms nothing); nothing is published."""
    w = World()
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        for user, status in ((w.VIEWER_A, 403), (w.DEV_B, 404)):
            started, _ = imported(w, client, user)
            assert started.status_code == status, started.text
        assert w.store.published == [] and w.store.actors == []


def test_lab_datasets__a_body_is_a_bounded_json_object_of_the_operation():
    """Past the bound 413; not JSON, not an object, or missing the operation's fields 400 -
    a refusal, never a 500 from inside N1."""
    w = World()
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        path = base(w.A) + "/imports"
        too_big = b'{"spec": "' + b"x" * ld.MAX_BODY_BYTES + b'"}'
        assert call(client, w.DEV_A, "POST", path, raw=too_big).status_code == 413
        for raw in (b"[1, 2]", b'"spec"', b'{"body": "x"}'):
            answer = call(client, w.DEV_A, "POST", path, raw=raw)
            assert answer.status_code == 400, (raw, answer.text)
        assert w.store.published == []
