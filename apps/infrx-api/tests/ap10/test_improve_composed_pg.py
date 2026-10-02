#!/usr/bin/env python3
"""AP-10 10e composed: the training-run routes (`routes/lab_improve.py`) over AP-04 as the Lab
unit composes it with LAB_ARTIFACTS - `artifacts.compose.surface` on the Lab control login
(`infrx_lab_control`) over 0060's `PgControlOps`, 0061's `PgArtifactStore` and real bytes on
MinIO - with AP-04's `artifacts` worker role (`artifacts.compose.role`, the factory `python -m
infrx.lab.workers artifacts` runs) passing in process, on ap10's task-local PostgreSQL and
MinIO. The source repository is a local stub (an httpx MockTransport serving the checkpoint's
adapter files at one commit): the process role cannot be pointed at a stub hub, so the role's
pass runs here with the stub as its `source`. The recording stand-in of `test_release.py` is
replaced; P3's D7/D8/B3 stay P3's fake world (their SQL proofs are `tests/p`'s on p3's key).

Oracles: the export is one finished operation; the approved checkpoint's registration is
AP-04's import operation in 0060 (readable at its Location, AP-04's operations read) and
one `infrx.artifact_imports` row; the evidence read is 409 while the operation is queued;
after the worker's pass verified the bytes on MinIO, the evidence names exactly the 0061
artifact row (id, manifest digest, repo, commit) and stays `qualification: pending`; another
key for the same checkpoint is 409 and leaves one import row.

    INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_improve_composed_pg.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import api
from infrx.contracts.tasklocal import local_services
from infrx.gateway import control
from infrx.gateway.routes import lab_artifacts, lab_improve
from infrx.lab.artifacts import compose
from infrx.lab.artifacts.imports import HubSource
from infrx.lab.artifacts.projects import ProjectRequest
from infrx.lab.improve import release
from infrx.state import migrations

from ..d import pgharness
from ..l.control import worlds as l3
from ..n.imports.world import NEMO
from ..p.training import world as p3world
from ..p.training.test_training import CONFIG, EXT
from .test_release import CKPT, Rig

TASK = os.environ.get("INFRX_D_TASK")
S3 = local_services("ap10").get("s3")
_reason = ("PostgreSQL + MinIO only on the ap10 task-local key (INFRX_D_TASK=ap10)"
           if TASK != "ap10" else
           "BLOCKED[WR-AP10E-2]: tasklocal key ap10 names no s3 port" if S3 is None else
           pgharness.unavailable())
pytestmark = pytest.mark.skipif(_reason is not None, reason=str(_reason))
DB = f"{pgharness.DATABASE}_ap10i"
LOGIN = "infrx_lab_control"
MINIO = "pgsty/minio@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372"
S3_USER, S3_SECRET, BUCKET = "infrxap10minio", "infrx-ap10-local-secret", "infrx-ap10"  # test-only
REPO, COMMIT = "NemoStation/Marlin-2B-SOP-LoRA", "fd111fca4fc7897876fb0d7e9df22ca5ac8ab965"
FILES = {"adapter_config.json": json.dumps({"r": 8, "base_model": "marlin-2b"}).encode(),
         "adapter_model.safetensors": b"\x08\x00\x00\x00\x00\x00\x00\x00{}" + b"\x00" * 56}


def run(coro):
    return asyncio.run(coro)


def entry(path: str) -> dict:
    blob = FILES[path]
    media = "application/json" if path.endswith(".json") else "application/octet-stream"
    return {"relative_path": path, "bytes": len(blob),
            "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(), "media_type": media}


def hub(request: httpx.Request) -> httpx.Response:
    """The source repository at one commit (public: no credential)."""
    prefix = f"/{REPO}/resolve/{COMMIT}/"
    blob = FILES.get(request.url.path.removeprefix(prefix)) \
        if request.url.path.startswith(prefix) else None
    return httpx.Response(404) if blob is None else httpx.Response(200, content=blob)


@pytest.fixture(scope="module")
def minio():
    assert S3 is not None
    subprocess.run(("docker", "rm", "-f", "-v", S3.container), capture_output=True)
    subprocess.run(("docker", "run", "-d", "--name", S3.container, "-p",
                    f"127.0.0.1:{S3.host_port}:9000", "-e", f"MINIO_ROOT_USER={S3_USER}",
                    "-e", f"MINIO_ROOT_PASSWORD={S3_SECRET}", MINIO, "server", "/data",
                    "--address", ":9000"), capture_output=True, check=True)
    try:
        os.environ.update(AWS_ACCESS_KEY_ID=S3_USER, AWS_SECRET_ACCESS_KEY=S3_SECRET,
                          AWS_DEFAULT_REGION="us-east-1")
        os.environ.pop("AWS_SESSION_TOKEN", None)
        from infrx.media.s3 import S3ObjectStore
        store = S3ObjectStore.connect(BUCKET, f"{S3.object_prefix}{uuid.uuid4().hex}/",
                                      f"http://127.0.0.1:{S3.host_port}")
        for _ in range(60):
            try:
                store.client.create_bucket(Bucket=BUCKET)
                break
            except Exception:                   # MinIO still starting  # noqa: BLE001
                time.sleep(0.5)
        yield store
    finally:
        subprocess.run(("docker", "rm", "-f", "-v", S3.container), capture_output=True)


@pytest.fixture(scope="module")
def login():
    from infrx.state.jobstore import connector
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    if pgharness.ON_SUPABASE:           # `postgres` is no superuser there: the login needs it
        pgharness._sb(DB, f"grant {LOGIN} to postgres with set true")
    with pgharness.connect(DB) as conn:
        l3.seed_pg(conn, pgharness.dsn(DB))
        yield SimpleNamespace(conn=conn, connect=connector(
            pgharness.dsn(DB) + f"&options=-c%20role%3D{LOGIN}", set_role=False))


def test_ap10_improve_composed_the_candidate_is_ap04s_verified_import_and_its_evidence(
        login, minio):
    source = HubSource({"huggingface.co": "http://hub.test"}, httpx.MockTransport(hub))
    artifacts = compose.surface(login.connect, minio, source=source)
    _, worker = compose.role("artifacts", {}, login.connect, minio, "ap10-worker", source=source)
    assert l3.PgWorld.A == NEMO, "P3's world and the seed share NemoStation"
    dev = api.Actor(audience="session", user_id=l3.PgWorld.DEV_A, provider_org_id=NEMO)
    project = run(artifacts.projects.create(dev, ProjectRequest(
        name="Marlin SOP", slug="marlin-sop"), "ap10-project-1"))
    r = Rig()                           # P3's fake world (with D7 0053's receipt read)
    x = lab_improve.LabImprove(access=artifacts.projects.access, store=r.w.store,
                               objects=r.w.objects, ledger=r.w.ledger,
                               ops=artifacts.ops.ops, artifacts=artifacts)
    app = FastAPI()
    rt = SimpleNamespace(actors=control.StaticActors(dev), lab_improve=x, lab_artifacts=artifacts)
    lab_improve.register(app, rt)
    lab_artifacts.register(app, rt)                       # the Location read is AP-04's
    c = TestClient(app)
    run_path = f"/lab/v1/providers/{NEMO}/training-runs/{EXT}"
    exported = c.post(f"{run_path}/export", headers={"Idempotency-Key": "ap10-export-1"}, json={
        "trainer": release.MANUAL, "dataset_ref": r.w.ref, "config": CONFIG,
        "label_export_id": r.label["export_id"], "payer_ref": p3world.PAYER,
        "limit": "25.00000000"})
    assert (exported.status_code, exported.json()["state"]) == (202, "succeeded"), exported.text
    descriptor = json.dumps({"format": "infrx.checkpoint.1", "base_model": "marlin-2b",
                             "adaptation": "lora", "files": sorted(FILES)}).encode()
    r.w.submit()
    key = r.w.put(descriptor, "ckpt-1")
    r.w.checkpoint(1, key=key, data=descriptor)
    r.w.evals.runs[CKPT]["state"] = "succeeded"
    r.w.approve()
    body = {"artifact_key": key, "project_id": project.project_id,
            "source": {"host": "huggingface.co", "repo": REPO, "commit": COMMIT},
            "files": [entry(p) for p in sorted(FILES)]}
    candidate = f"{run_path}/checkpoints/{CKPT}/candidate"
    started = c.post(candidate, json=body, headers={"Idempotency-Key": "ap10-candidate-1"})
    assert started.status_code == 202, started.text
    doc = started.json()
    assert (doc["kind"], doc["state"]) == ("artifact.import", "queued")
    evidence = f"{run_path}/checkpoints/{CKPT}/release-evidence"
    assert c.get(evidence).status_code == 409
    imports = "select count(*) from infrx.artifact_imports where project_id = %s"
    assert login.conn.execute(imports, (project.project_id,)).fetchone()[0] == 1
    assert run(worker.run_once()) >= 1
    read = c.get(started.headers["location"])
    assert (read.status_code, read.json()["state"]) == (200, "succeeded"), read.text
    got = c.get(evidence)
    assert got.status_code == 200, got.text
    record = got.json()
    row = login.conn.execute(
        "select artifact_id::text, manifest_sha256, source, source_repo, source_commit "
        "from infrx.artifacts where artifact_id::text = %s",
        (record["artifact"]["artifact_id"],)).fetchone()
    assert row is not None and row[2:] == ("import", REPO, COMMIT)
    assert (record["artifact"]["manifest_sha256"], record["artifact"]["operation_id"]) == \
        (row[1], doc["operation_id"])
    assert record["qualification"]["state"] == "pending"
    assert c.get(evidence).json() == record, "written once"
    again = c.post(candidate, json=body, headers={"Idempotency-Key": "ap10-candidate-2"})
    assert again.status_code == 409, again.text
    assert login.conn.execute(imports, (project.project_id,)).fetchone()[0] == 1
    stored = run(minio.keys(""))
    assert any(k.endswith("adapter_model.safetensors") for k in stored), "bytes on MinIO"
