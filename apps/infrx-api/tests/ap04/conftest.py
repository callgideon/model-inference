"""AP-04's two worlds over the routes as mounted (`register(app, rt)` on a bare FastAPI app):

- `fake`: L3's LAB-PUBLISH fake world (`tests/l/control/worlds.py`: providers A = NemoStation
  with the seeded Marlin serving version and B, their members, an operator), the fake row
  store, process-memory objects and operations;
- `pg` (marked `pg`, only on `INFRX_D_TASK=ap4`): the same world on the task-local
  PostgreSQL (port 57554, every migration including 0061; the AP-04 store connects as the
  Lab control login `infrx_lab_control`) and real bytes on the lane's MinIO (57555).

The source repository is a local stub (an httpx MockTransport serving a tiny Marlin-shaped
repository at one commit; its config, generation and processor files are the measured Marlin
copies under research/models/marlin2b). Identity comes from a test `ActorSource` keyed by a
request header: the routes never read identity from a body.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import time
import types
import uuid

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from infrx.contracts import api, errors
from infrx.contracts.tasklocal import local_services
from infrx.gateway.routes import lab_artifacts, lab_model_projects
from infrx.lab.artifacts import ArtifactWorker, LabArtifacts
from infrx.lab.artifacts.imports import HubSource
from infrx.lab.artifacts.store import FakeArtifactStore, MemoryControlOps, MemoryObjects

from tests.d import pgharness
from tests.l.control import worlds as l3

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
MARLIN = REPO_ROOT / "research" / "models" / "marlin2b"
SERVING_VERSION = REPO_ROOT / "models" / "marlin2b" / "serving-version.json"
ON_AP4 = os.environ.get("INFRX_D_TASK") == "ap4"
S3 = local_services("ap4")["s3"]
MINIO = "pgsty/minio@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372"
S3_USER, S3_SECRET, BUCKET = "infrxap4minio", "infrx-ap4-local-secret", "infrx-ap4"  # test-only
LOGIN = "infrx_lab_control"
COMMIT = "fd111fca4fc7897876fb0d7e9df22ca5ac8ab965"
REPO = "NemoStation/Marlin-2B"
TOKEN = "stub-source-token-ap4"             # the stub's credential (never a real token)
TEMPLATE, CASE = f"{pgharness.DATABASE}_ap4tpl", f"{pgharness.DATABASE}_ap4case"


def pytest_configure(config):
    config.addinivalue_line("markers", "pg: runs on the lane's task-local PostgreSQL + MinIO")


def run(coro):
    return asyncio.run(coro)


# ============================================================ the tiny Marlin repo ===
def marlin_files(**override: bytes | None) -> dict[str, bytes]:
    """A Marlin-shaped repository: the measured config/generation/processor files, a tiny
    tokenizer, template and two tiny shards with their index. `override` replaces a file's
    bytes, or drops it with None."""
    files = {name: (MARLIN / name).read_bytes() for name in (
        "config.json", "generation_config.json", "processor_config.json",
        "preprocessor_config.json")}
    files |= {"tokenizer.json": b'{"model": "tiny"}', "chat_template.jinja": b"{{ messages }}",
              "model-00001-of-00002.safetensors": b"\x00shard-one" * 64,
              "model-00002-of-00002.safetensors": b"\x01shard-two" * 32}
    files["model.safetensors.index.json"] = json.dumps({"weight_map": {
        "a.weight": "model-00001-of-00002.safetensors",
        "b.weight": "model-00002-of-00002.safetensors"}}).encode()
    for name, blob in override.items():
        if blob is None:
            files.pop(name, None)
        else:
            files[name] = blob
    return files


def entry(path: str, blob: bytes) -> dict:
    media = "application/json" if path.endswith(".json") else "application/octet-stream"
    return {"relative_path": path, "bytes": len(blob),
            "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(), "media_type": media}


def manifest(files: dict[str, bytes]) -> list[dict]:
    return [entry(p, b) for p, b in sorted(files.items())]


class Stub:
    """The source repository at one commit; requests are recorded (never their headers)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.seen, self.redirect = files, [], None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        if request.url.host == "evil.test":
            return httpx.Response(200, content=b"elsewhere")
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        prefix = f"/{REPO}/resolve/{COMMIT}/"
        path = request.url.path
        if not path.startswith(prefix):
            return httpx.Response(404)
        name = path[len(prefix):]
        if self.redirect == name:
            return httpx.Response(302, headers={"location": "http://evil.test/blob"})
        blob = self.files.get(name)
        return httpx.Response(404) if blob is None else httpx.Response(200, content=blob)


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
            "outsider": session(w.CONSUMER_ONLY, w.A),
            "ops": api.Actor(audience="session", user_id=w.OPS_USER, operator=True),
            "dev_a_as_ops": session(w.BOTH, w.A)}


class World:
    """One case's world: the app, its stores and helpers over HTTP."""

    def __init__(self, w, store, objects, s3_put) -> None:
        self.w, self.store, self.objects, self.s3_put = w, store, objects, s3_put
        self.A, self.B = w.A, w.B
        self.ops = MemoryControlOps(store.db_now)
        self.stub = Stub(marlin_files())
        self.secret = {"value": TOKEN}
        self.artifacts = LabArtifacts.compose(
            w.access, w.control, store, self.ops, objects,
            HubSource({"huggingface.co": "http://hub.test"}, httpx.MockTransport(
                self.stub.handler)),
            secret=lambda ref: self._secret(ref))
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        rt = types.SimpleNamespace(actors=TestActors(actors(w)), lab_artifacts=self.artifacts)
        lab_model_projects.register(app, rt)
        lab_artifacts.register(app, rt)
        self.app = app
        self.client = TestClient(app)
        self._keys = 0

    def _secret(self, ref: str) -> str:
        from infrx.lab.artifacts.imports import SourceRefused
        if ref != "env:HF_STUB_TOKEN":
            raise SourceRefused("secret_unavailable")
        return self.secret["value"]

    def key(self) -> str:
        self._keys += 1
        return f"ap04-key-{self._keys:04d}-{uuid.uuid4().hex[:6]}"

    def call(self, method: str, path: str, actor: str = "dev_a", key: str | None = None,
             **kw) -> httpx.Response:
        headers = {"x-test-actor": actor, **kw.pop("headers", {})}
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.client.request(method, path, headers=headers, **kw)

    def worker(self, owner: str | None = None) -> int:
        return run(ArtifactWorker(self.artifacts, owner).run_once())

    def advance(self, seconds: float) -> None:
        self.w.advance(seconds)

    def put(self, grant: dict, blob: bytes) -> None:
        url = grant["url"]
        if url.startswith("memory://"):
            self.objects.data[url.removeprefix("memory://").partition("?")[0]] = blob
        else:
            self.s3_put(url, blob)

    def stored_keys(self) -> list[str]:
        return run(self.objects.keys("artifacts/"))

    def serving(self, serving_version_id: str):
        return run(self.w.catalog.serving_revision(serving_version_id))

    def tables_text(self) -> str:
        """Every AP-04 row as stored, for the never-a-secret checks."""
        if isinstance(self.store, FakeArtifactStore):
            return json.dumps({t: [r.model_dump(mode="json") for r in rows.values()]
                               for t, rows in self.store.tables.items()})
        return json.dumps([list(map(str, r)) for t in (
            "model_projects", "artifacts", "artifact_uploads", "artifact_imports",
            "model_project_revisions") for r in self.w.conn.execute(
                f"select row_to_json(x)::text from infrx.{t} x").fetchall()])


# ===================================================================== fixtures ===
@pytest.fixture
def fake_world():
    w = l3.FakeWorld()
    c = w.control_store
    store = FakeArtifactStore(clock=w.store.db_now, slugs=dict(c.slugs), models=c.models)
    return World(w, store, MemoryObjects(), None)


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), capture_output=True, check=True)


@pytest.fixture(scope="session")
def minio():
    """MinIO on ap4's port, this suite's only; removed at the end."""
    for _ in range(12):
        subprocess.run(("docker", "rm", "-f", "-v", S3.container), capture_output=True)
        try:
            _docker("run", "-d", "--name", S3.container, "-p", f"127.0.0.1:{S3.host_port}:9000",
                    "-e", f"MINIO_ROOT_USER={S3_USER}", "-e", f"MINIO_ROOT_PASSWORD={S3_SECRET}",
                    MINIO, "server", "/data", "--address", ":9000")
            break
        except subprocess.CalledProcessError as failed:
            if b"address already in use" not in failed.stderr:
                raise
            time.sleep(10)
    try:
        os.environ.update(AWS_ACCESS_KEY_ID=S3_USER, AWS_SECRET_ACCESS_KEY=S3_SECRET,
                          AWS_DEFAULT_REGION="us-east-1")
        os.environ.pop("AWS_SESSION_TOKEN", None)
        from infrx.media.s3 import S3ObjectStore
        probe = S3ObjectStore.connect(BUCKET, S3.object_prefix,
                                      f"http://127.0.0.1:{S3.host_port}")
        for _ in range(60):
            try:
                probe.client.create_bucket(Bucket=BUCKET)
                break
            except Exception:                   # MinIO still starting  # noqa: BLE001
                time.sleep(0.5)
        yield
    finally:
        subprocess.run(("docker", "rm", "-f", "-v", S3.container), capture_output=True)


@pytest.fixture(scope="session")
def pg_template():
    if not ON_AP4:
        pytest.skip("PostgreSQL + MinIO only on the ap4 task-local key (INFRX_D_TASK=ap4)")
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
    """The Lab control login: the grants 0061 gives it are the ones this store uses."""
    return pgharness.dsn(database) + f"&options=-c%20role%3D{LOGIN}"


@pytest.fixture
def pg_world(pg_template, minio):
    from infrx.lab.artifacts.store import PgArtifactStore, S3Objects
    from infrx.media.s3 import S3ObjectStore
    from infrx.state.jobstore import connector
    pgharness.assert_ours("copy a database in")
    from psycopg import sql
    with pgharness.connect("postgres") as admin:
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(
            sql.Identifier(CASE)))
        admin.execute(sql.SQL("create database {} template {}").format(
            sql.Identifier(CASE), sql.Identifier(pg_template)))
    with pgharness.connect(CASE) as conn:
        w = l3.PgWorld(conn, pgharness.dsn(CASE))
        store = PgArtifactStore(connector(login_dsn(CASE), set_role=False))
        objects = S3Objects(S3ObjectStore.connect(
            BUCKET, f"{S3.object_prefix}{uuid.uuid4().hex}/", f"http://127.0.0.1:{S3.host_port}"))

        def s3_put(url: str, blob: bytes) -> None:
            assert httpx.put(url, content=blob, timeout=30).status_code == 200

        yield World(w, store, objects, s3_put)


@pytest.fixture(params=["fake", pytest.param("pg", marks=pytest.mark.pg)])
def world(request) -> World:
    return request.getfixturevalue(f"{request.param}_world")
