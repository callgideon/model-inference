"""AP-05's worlds: the routes as mounted (`register(app, rt)` on a bare FastAPI app) and the
hosting controller as the worker runs it.

- `fake`: L3's two-provider world (`tests/l/control/worlds.py`: A = NemoStation with the
  seeded production Marlin, B, their members, an operator), AP-04's row store holding one
  verified tiny Marlin-shaped artifact and the serving revision AP-04's own door
  (`Projects.create_revision`) built from it, 0060's `FakeControlOps`, `FakeHostingStore`, and
  a SCRIPTED engine: `ScriptedLauncher` "starts" an httpx MockTransport engine - no socket,
  no process (the fast list the mutants run);
- `pg` (marked `pg`, only on `INFRX_D_TASK=ap5`): the same world on ap5's PostgreSQL (57556,
  every migration through 0062; the AP-04 and hosting stores as the Lab control login
  `infrx_lab_control`) and a REAL engine process: `tests/integration/fake_vllm.py`'s app on
  ap5's engine port 57557, launched by `LocalLauncher` (`lab_hosting/candidate_engine.py`).

Identity comes from a test `ActorSource` keyed by a request header: the routes never read
identity from a body.
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import pathlib
import sys
import time
import types
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from infrx.contracts import api, errors
from infrx.contracts.tasklocal import local_services
from infrx.gateway.routes import lab_deployments
from infrx.lab.artifacts.manifest import Card, FileEntry, Manifest
from infrx.lab.artifacts.projects import Projects, RevisionRequest
from infrx.lab.artifacts.store import Artifact, FakeArtifactStore, Project
from infrx.lab.artifacts.verify import Compatibility
from infrx.lab.hosting import KINDS, PROFILE, LabHosting, Target
from infrx.lab.hosting.controller import Controller
from infrx.lab.hosting.engine import Runtime
from infrx.lab.hosting.store import FakeHostingStore
from infrx.state.control_ops import FakeControlOps

from tests.d import pgharness
from tests.l.control import worlds as l3

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
ON_AP5 = os.environ.get("INFRX_D_TASK") == "ap5"
SERVICES = local_services("ap5")
ENGINE_PORT = SERVICES["engine-fake"].host_port                       # 57557
CANDIDATE = REPO_ROOT / "tests" / "integration" / "lab_hosting" / "candidate_engine.py"
LOGIN = "infrx_lab_control"
TEMPLATE, CASE = f"{pgharness.DATABASE}_ap5tpl", f"{pgharness.DATABASE}_ap5case"
SLOT = "local-ap5/candidate-0"


def pytest_configure(config):
    config.addinivalue_line("markers", "pg: runs on the lane's task-local PostgreSQL + engine")


def run(coro):
    return asyncio.run(coro)


# ================================================================ the tiny artifact ===
def model_files(**override: bytes | None) -> dict[str, bytes]:
    """A Marlin-shaped model directory: two tiny shards, tokenizer, template, configs."""
    files = {"config.json": b'{"architectures": ["MarlinForConditionalGeneration"]}',
             "generation_config.json": b'{"temperature": 0.7}',
             "processor_config.json": b'{"processor": "tiny"}',
             "preprocessor_config.json": b'{"size": 448}',
             "tokenizer.json": b'{"model": "tiny"}', "chat_template.jinja": b"{{ messages }}",
             "model-00001-of-00002.safetensors": b"\x00shard-one" * 64,
             "model-00002-of-00002.safetensors": b"\x01shard-two" * 32,
             "model.safetensors.index.json": b'{"weight_map": {}}'}
    for name, blob in override.items():
        if blob is None:
            files.pop(name, None)
        else:
            files[name] = blob
    return files


def entries(files: dict[str, bytes]) -> tuple[FileEntry, ...]:
    return tuple(FileEntry(relative_path=p, bytes=len(b),
                           sha256="sha256:" + hashlib.sha256(b).hexdigest(),
                           media_type="application/json" if p.endswith(".json")
                           else "application/octet-stream") for p, b in sorted(files.items()))


def clip() -> bytes:
    """A finite MP4-shaped clip (ftyp + mdat boxes): the fake engine never decodes it, the
    real one is given a corpus clip by the box procedure."""
    return (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00\x00\x04\x08mdat"
            + bytes(range(256)) * 4)


# ============================================================= the scripted engine ===
SERVED = PROFILE.served_model_name


class ScriptedEngine:
    """What a fake-world engine answers: `/v1/models`, the smoke completion, `/metrics`."""

    def __init__(self) -> None:
        self.up, self.models, self.running = True, [SERVED], 0
        self.answer: dict = {"content": "a person walks in", "prompt_tokens": 1200,
                             "completion_tokens": 6, "finish_reason": "stop", "status": 200}
        self.seen: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        if request.url.path == "/metrics":
            return httpx.Response(200, text=f'vllm:num_requests_running{{model_name="m"}} '
                                            f'{self.running}\n')
        if request.url.path == "/v1/chat/completions":
            if self.answer.get("chat_down"):
                raise httpx.ConnectError("refused", request=request)
            body = json.loads(request.content)
            self.seen.append(body)
            a = self.answer
            if a["status"] != 200:
                return httpx.Response(a["status"], json={"error": {"message": "no"}})
            return httpx.Response(200, json={
                "model": a.get("model", body["model"]), "choices": [{"message": {"content": a["content"]},
                                                     "finish_reason": a["finish_reason"]}],
                "usage": {"prompt_tokens": a["prompt_tokens"],
                          "completion_tokens": a["completion_tokens"]}})
        return httpx.Response(404)


class ScriptedLauncher:
    """`Launcher` over in-memory engines: what a started engine runs is `image`/`flags`."""

    def __init__(self) -> None:
        self.running: dict[str, Runtime] = {}
        self.starts: list[str] = []
        self.stops: list[str] = []
        self.image = PROFILE.runtime_image_ref.partition("@")[2]
        self.flags = PROFILE.flags
        self.model_dir: str | None = None          # a launch that loads another directory

    async def start(self, allocation, model_dir) -> None:
        self.starts.append(allocation.resource_tag)
        self.running[allocation.resource_tag] = Runtime(
            image=self.image, flags=tuple(self.flags), model_dir=self.model_dir or str(model_dir),
            declared=True)

    async def inspect(self, allocation) -> Runtime | None:
        return self.running.get(allocation.resource_tag)

    async def stop(self, allocation) -> bool:
        self.stops.append(allocation.resource_tag)
        return self.running.pop(allocation.resource_tag, None) is not None


def target_in(tmp: pathlib.Path) -> Target:
    """The world's one slot: ap5's engine port, models installed under `tmp/models`."""
    return Target(slot=SLOT, port=ENGINE_PORT, model_root=tmp / "models",
                  source_dir=tmp / "source", smoke_video=tmp / "clip.mp4")


# ======================================================================= actors ===
class TestActors:
    """`ActorSource` for the suite: the `x-test-actor` header names a fixed actor."""

    def __init__(self, actors: dict[str, api.Actor]) -> None:
        self.actors = actors

    async def actor(self, request: Request) -> api.Actor:
        name = request.headers.get("x-test-actor")
        if name not in self.actors:
            raise errors.InvalidApiKey("no session")
        return self.actors[name]


def actors(w) -> dict[str, api.Actor]:
    def session(user, provider):
        return api.Actor(audience="session", user_id=user, provider_org_id=provider)
    return {"dev_a": session(w.BOTH, w.A), "admin_a": session(w.ADMIN_A, w.A),
            "viewer_a": session(w.VIEWER_A, w.A), "dev_b": session(w.DEV_B, w.B),
            "outsider": session(w.CONSUMER_ONLY, w.A)}


# ======================================================================== world ===
class World:
    def __init__(self, w, artifacts, store, ops, launcher, transport, tmp: pathlib.Path,
                 real: bool) -> None:
        self.w, self.artifacts, self.store, self.ops = w, artifacts, store, ops
        self.launcher, self.transport, self.real, self.tmp = launcher, transport, real, tmp
        self.A, self.B = w.A, w.B
        self.files = model_files()
        (tmp / "source").mkdir()
        for name, blob in self.files.items():
            (tmp / "source" / name).write_bytes(blob)
        (tmp / "clip.mp4").write_bytes(clip())
        self.target = target_in(tmp)
        self.hosting = LabHosting(w.access, w.control, artifacts, ops, store, self.target)
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        rt = types.SimpleNamespace(actors=TestActors(actors(w)), lab_hosting=self.hosting)
        lab_deployments.register(app, rt)
        self.app, self.client = app, TestClient(app)
        self.actors = actors(w)
        self.serving = run(self._seed())
        self._keys = 0

    async def _seed(self) -> str:
        """One verified artifact of A's project and the serving revision AP-04 builds."""
        dev = self.actors["dev_a"]
        now = await self.artifacts.db_now()
        project = await self.artifacts.insert(Project(
            project_id=str(uuid.uuid4()), provider_org_id=self.A, slug="tiny-marlin",
            name="Tiny Marlin", description="", request_hash="sha256:" + "1" * 64,
            created_by=dev.user_id, created_at=now))
        files = entries(self.files)
        artifact = await self.artifacts.insert(Artifact(
            artifact_id=str(uuid.uuid4()), project_id=project.project_id, provider_org_id=self.A,
            source="upload", files=files, manifest_sha256=Manifest(files=files).digest,
            card=Card(), provenance={}, compatibility=Compatibility(supported=True),
            verified_at=now, created_by=dev.user_id, created_at=now))
        doc = await Projects(self.w.access, self.artifacts, self.w.control).create_revision(
            dev, project.project_id, RevisionRequest(artifact_id=artifact.artifact_id), "seed-1")
        return doc.serving_version_id

    # --- HTTP ------------------------------------------------------------------------
    def key(self) -> str:
        self._keys += 1
        return f"ap05-key-{self._keys:04d}-{uuid.uuid4().hex[:6]}"

    def call(self, method: str, path: str, actor: str = "dev_a", key: str | None = None,
             **kw) -> httpx.Response:
        headers = {"x-test-actor": actor, **kw.pop("headers", {})}
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.client.request(method, path, headers=headers, **kw)

    def body(self, **change) -> dict:
        return {"serving_version_id": self.serving, "hosting_profile": PROFILE.profile_id,
                "endpoint_name": "tiny-marlin", "max_input_tokens": 16384,
                "max_output_tokens": 1024, "expire_after_s": 7200, **change}

    def deploy(self, actor: str = "dev_a", key: str | None = None, **change) -> httpx.Response:
        return self.call("POST", "/lab/v1/control/deployments", actor, key or self.key(),
                         json=self.body(**change))

    def deployed(self) -> tuple[str, str]:
        """A fresh deployment request: (operation id, deployment id)."""
        answer = self.deploy()
        assert answer.status_code == 202, answer.text
        return answer.json()["operation_id"], answer.json()["resource_id"]

    def detail(self, deployment_id: str, actor: str = "dev_a") -> dict:
        answer = self.call("GET", f"/lab/v1/control/deployments/{deployment_id}", actor)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def readiness(self, deployment_id: str) -> dict:
        answer = self.call("GET", f"/lab/v1/control/deployments/{deployment_id}/readiness")
        assert answer.status_code == 200, answer.text
        return answer.json()

    def op(self, operation_id: str) -> api.OperationDoc:
        return run(self.ops.get(operation_id, self.actors["dev_a"])).doc()

    # --- the controller ---------------------------------------------------------------
    def controller(self, owner: str | None = None, boundary=None, **kw) -> Controller:
        return Controller(self.hosting, self.launcher, self.target, owner=owner,
                          boundary=boundary, transport=self.transport, **kw)

    def drive(self, controller: Controller | None = None, passes: int = 6) -> None:
        """Passes until no operation is pending or held (at least two; a real engine is given
        up to 90 s of wall time to start)."""
        controller = controller or self.controller()
        deadline = time.monotonic() + 90
        for i in itertools.count(1):
            run(controller.run_once())
            busy = controller.holding or run(self.ops.pending(KINDS, 100))
            if (not busy and i >= 2) or (i >= passes and (not self.real
                                                          or time.monotonic() > deadline)):
                return
            if self.real:
                time.sleep(0.2)

    def advance(self, seconds: float) -> None:
        self.w.advance(seconds)

    def drafts(self) -> int:
        return sum(d.state.value == "draft" for d in self.w.control_store.deployments.values())

    def state(self, deployment_id: str) -> str:
        return run(self.w.control.store.deployment(deployment_id)).state.value


# ===================================================================== fixtures ===
@pytest.fixture
def fake_world(tmp_path):
    w = l3.FakeWorld()
    c = w.control_store
    artifacts = FakeArtifactStore(clock=w.store.db_now, slugs=dict(c.slugs), models=c.models)
    ops = FakeControlOps(now=lambda: w.store.now)
    store = FakeHostingStore(ops=ops, control=c, now=lambda: w.store.now)
    engine = ScriptedEngine()
    world = World(w, artifacts, store, ops, ScriptedLauncher(), httpx.MockTransport(
        engine.handler), tmp_path, real=False)
    world.engine = engine
    return world


@pytest.fixture(scope="session")
def pg_template():
    if not ON_AP5:
        pytest.skip("PostgreSQL + a real engine process only on the ap5 key (INFRX_D_TASK=ap5)")
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(TEMPLATE)
    pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    if pgharness.ON_SUPABASE:           # `postgres` is no superuser there: the login needs it
        pgharness._sb(TEMPLATE, f"grant {LOGIN} to postgres with set true")
    with pgharness.connect(TEMPLATE) as conn:
        l3.seed_pg(conn, pgharness.dsn(TEMPLATE))
    return TEMPLATE


def login_dsn(database: str) -> str:
    """The Lab control login: the grants 0060-0062 give it are the ones these stores use."""
    return pgharness.dsn(database) + f"&options=-c%20role%3D{LOGIN}"


class Recorded:
    """A real launcher's calls as the cases read them (`starts`, `stops`, `running`: the
    engines alive under a state file now)."""

    def __init__(self, inner) -> None:
        self.inner, self.starts, self.stops = inner, [], []

    async def start(self, allocation, model_dir) -> None:
        self.starts.append(allocation.resource_tag)
        await self.inner.start(allocation, model_dir)

    async def inspect(self, allocation):
        return await self.inner.inspect(allocation)

    async def stop(self, allocation) -> bool:
        self.stops.append(allocation.resource_tag)
        return await self.inner.stop(allocation)

    @property
    def running(self) -> dict[str, Runtime]:
        found = {}
        for path in sorted(self.inner.state_dir.glob("*.json")):
            held = types.SimpleNamespace(resource_tag=path.stem)
            runtime = run(self.inner.inspect(held))
            if runtime is not None:
                found[path.stem] = runtime
        return found


def local_launcher(state_dir: pathlib.Path, **kw):
    from infrx.lab.hosting.engine import LocalLauncher
    argv = (sys.executable, str(CANDIDATE), "--port", "{port}", "--model-dir", "{model_dir}",
            "--", *kw.pop("flags", PROFILE.flags))
    return LocalLauncher(argv, state_dir, image=kw.pop(
        "image", PROFILE.runtime_image_ref.partition("@")[2]))


@pytest.fixture
def pg_world(pg_template, tmp_path):
    from psycopg import sql

    from infrx.lab.artifacts.store import PgArtifactStore
    from infrx.lab.hosting.store import PgHostingStore
    from infrx.state.control_ops import PgControlOps
    from infrx.state.jobstore import connector
    pgharness.assert_ours("copy a database in")
    with pgharness.connect("postgres") as admin:
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(
            sql.Identifier(CASE)))
        admin.execute(sql.SQL("create database {} template {}").format(
            sql.Identifier(CASE), sql.Identifier(pg_template)))
    with pgharness.connect(CASE) as conn:
        w = l3.PgWorld(conn, pgharness.dsn(CASE))
        login = connector(login_dsn(CASE), set_role=False)
        launcher = Recorded(local_launcher(tmp_path / "engines"))
        world = World(w, PgArtifactStore(login), PgHostingStore(login), PgControlOps(login),
                      launcher, None, tmp_path, real=True)
        world.dsn, world.service_dsn = login_dsn(CASE), pgharness.dsn(CASE)
        try:
            yield world
        finally:
            for name in os.listdir(tmp_path / "engines") if (tmp_path / "engines").exists() \
                    else ():
                state = json.loads((tmp_path / "engines" / name).read_text())
                try:
                    os.killpg(state["pid"], 9)
                except (ProcessLookupError, PermissionError):
                    pass


@pytest.fixture(params=["fake", pytest.param("pg", marks=pytest.mark.pg)])
def world(request) -> World:
    return request.getfixturevalue(f"{request.param}_world")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
