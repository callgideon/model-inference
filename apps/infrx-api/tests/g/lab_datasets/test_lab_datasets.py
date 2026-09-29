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

import asyncio
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.datasets import imports
from infrx.gateway.routes import lab_datasets as ld
from infrx.media.store import InMemoryObjectStore

from .. import support
from ...l.access.worlds import FakeWorld
from ...n.imports.test_import import FakeJobs
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


class Jobs(FakeJobs):
    """0055's requeue beside 0051's queue: the provider's failed job again as the new id, the
    requeuer its actor; a replay is the one successor; any other state (or a rejected job)
    `StateConflict`, and so is a new id another job holds."""

    async def requeue(self, job_id, *, new_job_id, provider_org_id, actor):
        job = await self.job(job_id, provider_org_id=provider_org_id)
        if job["state"] != "failed" or job["error"] == "rejected":
            raise errors.StateConflict(f"only a failed import job is requeued; this one is "
                                       f"{job['state']}")
        again = next((j for j in self.rows.values() if j.get("requeued_from") == job_id), None)
        if again is None and new_job_id in self.rows:
            raise errors.StateConflict("that new job id is another job's")
        if again is None:
            again = await self.enqueue(new_job_id, {**job["spec"], "actor": actor},
                                       provider_org_id=provider_org_id, actor=actor)
            again["requeued_from"] = job_id
        return {"result": None, "error": None, **again}


class World(FakeWorld):
    def __init__(self) -> None:
        super().__init__()
        self.sessions = Sessions((self.DEV_A, self.DEV_B, self.VIEWER_A, self.CONSUMER_ONLY))
        self.store, self.objects, self.jobs = Store(), InMemoryObjectStore(), Jobs()
        self.store.add_grant(provider=self.A)

    def datasets(self) -> ld.LabDatasets:
        return ld.LabDatasets(self.sessions, self.access, self.store, self.objects, self.jobs)

    def pool(self) -> dict:
        """One pass of the I5 datasets pool (WR-N4-3's worker half) over this world."""
        return asyncio.run(imports.work(self.jobs, self.store, self.objects, worker_id="w",
                                        limit=10))

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
    w.pool()
    return started, call(client, user, "GET", base(w.A) + f"/imports/{w.spec()['import_id']}")


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


def test_lab_datasets__an_import_job_is_read_only_by_its_own_provider():
    """0051's job is read for the path's provider only: a developer of B polling A's import
    id under B's path gets a 404, never A's report."""
    w = World()
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        _, job = imported(w, client, w.DEV_A)
        assert job.json().get("state") == "published", job.text
        import_id = w.spec()["import_id"]
        foreign = call(client, w.DEV_B, "GET", base(w.B) + f"/imports/{import_id}")
        assert foreign.status_code == 404, foreign.text
        assert "dataset_ref" not in foreign.text


def test_lab_datasets__an_import_is_one_durable_job_the_pool_works(monkeypatch):
    """WR-C5-N4-ROUTE: POST imports enqueues ONE job on 0051's queue (`imports.enqueue`: the
    rows write-once beside the bundle, the session's user as the actor) and publishes nothing
    itself - the job reads `running` until the I5 pool works it; a re-POST is the same job.
    GET reads 0051's job: `succeeded` is `published` with its report (then listed), a
    refused-rows failure `rejected` with its report, any other failure `failed` with its
    reason. Without a queue the import routes are a 503."""
    w = World()
    _, data = fixture("benchmark")
    body = {"spec": w.spec(), "body": data.decode(), "accept_rejects": True}
    path, import_id = base(w.A) + "/imports", w.spec()["import_id"]
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        for _ in range(2):
            started = call(client, w.DEV_A, "POST", path, body)
            assert (started.status_code, started.json()) == (200, {
                "import_id": import_id, "state": "running", "report": None, "error": None})
        assert list(w.jobs.rows) == [import_id] and w.store.published == []
        assert w.jobs.rows[import_id]["spec"]["actor"] == w.DEV_A
        assert asyncio.run(w.objects.get(imports.rows_key(w.A, import_id))) == data
        assert call(client, w.DEV_A, "GET", f"{path}/{import_id}").json()["state"] == "running"
        assert w.pool() == {"succeeded": 1, "failed": 0, "retry": 0}
        job = call(client, w.DEV_A, "GET", f"{path}/{import_id}").json()
        assert (job["state"], job["error"]) == ("published", None)
        assert job["report"]["dataset_ref"] == w.store.published[-1]
        listed = call(client, w.DEV_A, "GET", base(w.A) + "/versions").json()
        assert [v["dataset_ref"] for v in listed] == [job["report"]["dataset_ref"]]

    rejected, refused = World(), imports.ImportRejected(imports.ImportReport(
        accepted=0, rejected=[{"line": 1, "reason": "grant", "detail": ""}], dataset_ref=None,
        source_ref=None))

    async def refuse(self, *args, **kw):
        raise refused
    monkeypatch.setattr(imports.Importer, "run", refuse)
    with TestClient(rejected.app(), raise_server_exceptions=False) as client:
        assert call(client, rejected.DEV_A, "POST", path, body).status_code == 200
        rejected.pool()
        job = call(client, rejected.DEV_A, "GET", f"{path}/{import_id}").json()
        assert (job["state"], job["report"], job["error"]) == \
            ("rejected", vars(refused.report), None)

    async def missing(self, *args, **kw):
        raise errors.NotFound("the source is gone")
    failed = World()
    monkeypatch.setattr(imports.Importer, "run", missing)
    with TestClient(failed.app(), raise_server_exceptions=False) as client:
        assert call(client, failed.DEV_A, "POST", path, body).status_code == 200
        failed.pool()
        job = call(client, failed.DEV_A, "GET", f"{path}/{import_id}").json()
        assert (job["state"], job["report"], job["error"]) == \
            ("failed", None, "the source is gone")

    unwired = World()
    unwired.jobs = None
    with TestClient(unwired.app(), raise_server_exceptions=False) as client:
        assert call(client, unwired.DEV_A, "POST", path, body).status_code == 503
        assert call(client, unwired.DEV_A, "GET", f"{path}/{import_id}").status_code == 503
        assert unwired.store.published == []


def test_lab_datasets__a_failed_import_is_requeued_as_a_new_job_the_pool_works(monkeypatch):
    """WR-C6-REQUEUE: `POST imports/{id}/requeue` turns the provider's FAILED job into a new
    queued job (0055; the failed one stays failed, R243) under an id derived from the failed
    one, after copying the upload's rows to that id - so the pool imports it unchanged and it
    reads `published`. A retry is the same job and writes nothing; a job that has not failed
    is a 409 that copies nothing (R1); a viewer 403, another provider's developer 404; an id
    another provider's import already holds never blocks it (R2); without a queue 503."""
    w = World()
    _, data = fixture("benchmark")
    body = {"spec": w.spec(), "body": data.decode(), "accept_rejects": True}
    path, import_id = base(w.A) + "/imports", w.spec()["import_id"]
    run = imports.Importer.run

    async def missing(self, *args, **kw):
        raise errors.NotFound("the source is gone")
    with TestClient(w.app(), raise_server_exceptions=False) as client:
        again = f"{path}/{import_id}/requeue"
        assert call(client, w.DEV_A, "POST", path, body).status_code == 200
        stored = len(w.objects.objects)
        assert call(client, w.DEV_A, "POST", again).status_code == 409      # still running
        assert len(w.objects.objects) == stored, "a refused requeue copied the upload's rows"
        derived = str(uuid.uuid5(uuid.NAMESPACE_URL, f"requeue:{w.A}:{import_id}"))
        w.jobs.rows[derived] = {"job_id": derived, "provider_org_id": w.B, "spec": {},
                                "state": "succeeded", "by": None, "lapsed": False,
                                "attempts": 1}          # another provider's import holds it
        monkeypatch.setattr(imports.Importer, "run", missing)
        w.pool()
        monkeypatch.setattr(imports.Importer, "run", run)
        for user, status in ((w.VIEWER_A, 403), (w.DEV_B, 404)):
            assert call(client, user, "POST", again).status_code == status, user
        made = call(client, w.DEV_A, "POST", again)
        assert made.status_code == 200, made.text
        new = made.json()["import_id"]
        assert made.json() == {"import_id": new, "state": "running", "report": None,
                               "error": None} and new not in (import_id, derived)
        assert w.jobs.rows[new].get("requeued_from") == import_id
        assert w.jobs.rows[new]["spec"]["actor"] == w.DEV_A
        assert asyncio.run(w.objects.get(imports.rows_key(w.A, new))) == data
        stored = len(w.objects.objects)
        assert call(client, w.DEV_A, "POST", again).json()["import_id"] == new   # a retry
        assert len(w.objects.objects) == stored and len(w.jobs.rows) == 3
        assert w.pool() == {"succeeded": 1, "failed": 0, "retry": 0}
        assert call(client, w.DEV_A, "GET", f"{path}/{new}").json()["state"] == "published"
        assert call(client, w.DEV_A, "GET", f"{path}/{import_id}").json()["state"] == "failed"
        assert call(client, w.DEV_A, "POST", f"{path}/{new}/requeue").status_code == 409
    unwired = World()
    unwired.jobs = None
    with TestClient(unwired.app(), raise_server_exceptions=False) as client:
        assert call(client, unwired.DEV_A, "POST", f"{path}/{import_id}/requeue").status_code \
            == 503
