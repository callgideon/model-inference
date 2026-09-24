#!/usr/bin/env python3
"""M5 item 4 / UPLOAD-RESTART on the stack: the real router, MinIO and PostgreSQL.

    INFRX_D_TASK=m5 INFRX_M_S3_ENDPOINT=http://127.0.0.1:55470 INFRX_M_S3_LOCAL_CREDS=1 \\
        INFRX_M_S3_BUCKET=infrx-m5 uv run --frozen pytest -q tests/m/test_upload_restart_stack.py

Two halves, each skipping visibly when its service is missing:

* `test_stack__minio_*` - the composed gateways of `test_upload_wiring.py` (one app per
  process, F2C's reference ticket authority) over a real S3-compatible store: write-once
  destinations are MinIO's `If-None-Match`, digests are MinIO's verified SHA-256.
* `test_stack__os_processes_*` - every step a SEPARATE OS PROCESS (`upload_stack_step.py`)
  over D10's `PgLifecycle` on the task-local database and MinIO: create in A, PUT in B,
  complete in C, admit in D; a process killed (`os._exit`) right after each durable step and
  the next call made by another; duplicate/reordered calls; the window on the database
  clock; two tenants; and the negative control, today's composition, failing at B. Rows and
  object keys are snapshotted before and after recovery (no customer content: digests,
  sizes, states, times, server-built keys).

Objects live under `test/m5/<uuid>/` of the bucket and are removed after each case.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid

import pytest
from infrx.media.s3 import S3ObjectStore

from ..d import pgharness
from .test_s3 import ACCESS_KEY, LOCAL_FLAG, SECRET_KEY, s3_env
from .test_upload_restart import DIGEST, OTHER_CLIP
from .test_uploads import CLIP, TTL

API_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENDPOINT = os.environ.get("INFRX_M_S3_ENDPOINT", "")
BUCKET = os.environ.get("INFRX_M_S3_BUCKET", "infrx-m5")
PREFIX_RE = re.compile(r"test/m5/[0-9a-f]{32}/")
UPLOAD = "infrx-upload:"
DIED = 17

needs_s3 = pytest.mark.skipif(not ENDPOINT, reason=(
    "M5 (owner: M): no S3-compatible endpoint - start the task-local MinIO (infrx-m5-s3) and "
    "export INFRX_M_S3_ENDPOINT, INFRX_M_S3_LOCAL_CREDS=1"))
_pg = pgharness.unavailable()
_d10 = None if importlib.util.find_spec("infrx.state.lifecycle") else (
    "D10's PgLifecycle (infrx/state/lifecycle.py) has not landed on this branch")
needs_stack = pytest.mark.skipif(bool(_pg or _d10 or not ENDPOINT), reason=(
    f"M5 stack drill needs MinIO, the task-local PostgreSQL and D10: "
    f"{_d10 or _pg or 'no INFRX_M_S3_ENDPOINT'}"))


@pytest.fixture
def minio(monkeypatch):
    """A store under a prefix of its own on the task-local MinIO, emptied afterwards."""
    s3_env(monkeypatch)
    prefix = f"test/m5/{uuid.uuid4().hex}/"
    objects = S3ObjectStore.connect(BUCKET, prefix, ENDPOINT)
    if os.environ.get(LOCAL_FLAG) == "1":
        from botocore.exceptions import ClientError
        try:
            objects.client.head_bucket(Bucket=BUCKET)
        except ClientError:
            objects.client.create_bucket(Bucket=BUCKET)
    yield objects
    if not PREFIX_RE.fullmatch(objects.prefix):              # never anything but ours
        raise ValueError(f"refusing to empty {objects.prefix!r}")
    for page in objects.client.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=objects.prefix):
        batch = [{"Key": item["Key"]} for item in page.get("Contents", ())]
        if batch:
            objects.client.delete_objects(Bucket=BUCKET, Delete={"Objects": batch})


def keys(objects) -> list[str]:
    """The media objects under the case's prefix (the staged request payloads aside)."""
    import asyncio
    return [key for key in asyncio.run(objects.keys("")) if not key.startswith("payloads/")]


# --- MinIO: the composed gateways over a real S3-compatible store ------------------------------
@needs_s3
def test_stack__minio_the_mounted_sequence_across_composed_gateways(monkeypatch, minio):
    """Create in A, PUT in B (MinIO's write-once destination), complete in C, retry in D,
    admit in E; other bytes to the same destination are 409 and MinIO keeps the first; a
    foreign PUT stores nothing; the source object carries MinIO's own SHA-256."""
    from .test_upload_wiring import (CONSUMER, OTHER_AUTH, VIDEO, Deployment, call, code,
                                     job_body)
    deployment = Deployment(monkeypatch)
    deployment.objects = minio
    a, b, c, d, e = (deployment.gateway() for _ in range(5))
    created = call(a, "POST", "/v1/uploads", headers=CONSUMER, json={
        "bytes": len(CLIP), "digest": DIGEST, "accepted_mime": ["video/mp4"]})
    assert created.status_code == 201, created.text
    handle = created.json()["upload_handle"]
    assert created.json()["destination_ref"] == UPLOAD + handle
    stored = call(b, "PUT", f"/v1/uploads/{handle}", content=CLIP,
                  headers={**CONSUMER, **VIDEO})
    assert stored.status_code == 204, stored.text
    foreign = call(c, "PUT", f"/v1/uploads/{handle}", content=CLIP,
                   headers={**OTHER_AUTH, **VIDEO})
    assert (foreign.status_code, code(foreign)) == (404, "not_found")
    other = call(c, "PUT", f"/v1/uploads/{handle}", content=OTHER_CLIP,
                 headers={**CONSUMER, **VIDEO})
    assert (other.status_code, code(other)) == (409, "state_conflict")
    done = call(c, "POST", f"/v1/uploads/{handle}/complete", headers=CONSUMER)
    assert done.status_code == 200, done.text
    assert (done.json()["media"]["digest"], done.json()["media"]["bytes"]) == (DIGEST,
                                                                                len(CLIP))
    retry = call(d, "POST", f"/v1/uploads/{handle}/complete", headers=CONSUMER)
    assert retry.content == done.content
    accepted = call(e, "POST", "/v1/jobs", headers=CONSUMER, json=job_body(handle))
    assert accepted.status_code == 202, accepted.text
    (job,) = deployment.jobs.jobs.values()
    (ref,) = job.request.media
    import asyncio
    assert asyncio.run(minio.head(ref.storage_ref)) == DIGEST           # MinIO measured it
    assert asyncio.run(minio.get(f"uploads/{ref.org_id}/{handle}")) == CLIP
    assert keys(minio) == sorted([ref.storage_ref, f"uploads/{ref.org_id}/{handle}"])


# --- separate OS processes on PostgreSQL (D10) and MinIO -------------------------------------
class Stack:
    """The deployment the child processes share: a fresh migrated CREDIT database (its clock
    frozen, moved only by the parent) and a MinIO prefix. `step()` is one gateway process."""

    def __init__(self, minio, tmp_path, *, durable: bool = True) -> None:
        self.objects, self.tmp, self.dsn = minio, tmp_path, ""
        if durable:
            from infrx.state import migrations, pgtesting

            from ..d import pgstore
            factory = pgtesting.make_credit_jobstore_factory(
                pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text())
            self.harness = factory()
            self.dsn = pgharness.dsn(self.harness.extra["database"])
            self.conn = self.harness.extra["conn"]
            self.clock = self.harness.clock
        self.env = {**os.environ, "M5_DSN": self.dsn, "M5_S3_ENDPOINT": ENDPOINT,
                    "M5_S3_BUCKET": BUCKET, "M5_S3_PREFIX": minio.prefix,
                    "AWS_ACCESS_KEY_ID": ACCESS_KEY, "AWS_SECRET_ACCESS_KEY": SECRET_KEY,
                    "AWS_DEFAULT_REGION": "us-east-1", "AWS_EC2_METADATA_DISABLED": "true",
                    "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull}
        for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL",
                     "AWS_ENDPOINT_URL_S3"):
            self.env.pop(name, None)

    def step(self, method, path, *, data=None, died=False, **options) -> dict:
        argv = [sys.executable, "-m", "tests.m.upload_stack_step", method, path]
        if data is not None:
            body = self.tmp / f"body-{uuid.uuid4().hex}"
            body.write_bytes(data)
            argv += ["--data", str(body)]
        for name, value in options.items():
            argv += [f"--{name.replace('_', '-')}",
                     json.dumps(value) if name == "json" else str(value)]
        done = subprocess.run(argv, cwd=API_DIR, env=self.env, capture_output=True,
                              text=True, timeout=180)
        if died:
            assert done.returncode == DIED, (done.returncode, done.stderr[-1500:])
            return {}
        assert done.returncode == 0, done.stderr[-3000:]
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        return answer

    def rows(self, handle) -> dict:
        """The ticket's durable row, as PostgreSQL holds it (sanitized: no content)."""
        row = self.conn.execute("select to_jsonb(u) from infrx.media_uploads u "
                                "where handle = %s", (handle,)).fetchone()
        return row[0] if row else {}

    def keys(self) -> list[str]:
        return keys(self.objects)


def create(stack, **options):
    answer = stack.step("POST", "/v1/uploads", json={
        "bytes": len(CLIP), "digest": DIGEST, "accepted_mime": ["video/mp4"]}, **options)
    assert answer["status"] == 201, answer
    return answer["body"]["upload_handle"]


def job_body(handle):
    from .test_upload_wiring import job_body as body
    return body(handle)


@needs_s3
def test_stack__os_processes_the_process_local_authority_fails_at_the_second_process(
        minio, tmp_path):
    """Negative control on the stack: today's composition (`--authority process`) - the
    ticket OS process A issued is `404 not_found` to OS process B, and MinIO holds nothing
    for it."""
    stack = Stack(minio, tmp_path, durable=False)
    handle = create(stack, authority="process")
    put = stack.step("PUT", f"/v1/uploads/{handle}", data=CLIP, authority="process")
    assert (put["status"], put["body"]["error"]["code"]) == (404, "not_found")
    assert stack.keys() == []


@needs_stack
def test_stack__os_processes_create_put_complete_admit(minio, tmp_path):
    """Create in A, PUT in B, complete in C, a retried completion in D, admission in E -
    five OS processes - with the durable row and the object keys after each step."""
    stack = Stack(minio, tmp_path)
    handle = create(stack)
    assert stack.rows(handle)["state"] == "created" and stack.keys() == []
    put = stack.step("PUT", f"/v1/uploads/{handle}", data=CLIP)
    assert put["status"] == 204, put
    org = stack.rows(handle)["org_id"]
    assert stack.keys() == [f"uploads/{org}/{handle}"]
    done = stack.step("POST", f"/v1/uploads/{handle}/complete")
    assert done["status"] == 200, done
    media = done["body"]["media"]
    assert (media["digest"], media["bytes"], media["kind"]) == (DIGEST, len(CLIP), "upload")
    row = stack.rows(handle)
    assert row["state"] == "finalized" and row["digest"] == DIGEST
    retry = stack.step("POST", f"/v1/uploads/{handle}/complete")
    assert retry["body"] == done["body"]
    admitted = stack.step("POST", "/v1/jobs", json=job_body(handle))
    assert admitted["status"] == 202, admitted
    (ref,) = admitted["admitted"]
    assert (ref["handle"], ref["digest"], ref["bytes"]) == (handle, DIGEST, len(CLIP))
    assert stack.keys() == sorted([ref["storage_ref"], f"uploads/{org}/{handle}"])


@needs_stack
@pytest.mark.parametrize("crash", ["acknowledge_put", "put_if_absent:uploads/",
                                   "put_if_absent:media/", "complete"])
def test_stack__os_processes_a_crash_after_each_durable_step(minio, tmp_path, crash):
    """The process is killed right after one durable step; another process retries and the
    upload finishes with exactly one destination and one source object - the row and the
    keys are recorded before and after the recovery."""
    stack = Stack(minio, tmp_path)
    handle = create(stack)
    if crash in ("acknowledge_put", "put_if_absent:uploads/"):
        stack.step("PUT", f"/v1/uploads/{handle}", data=CLIP, die_after=crash, died=True)
        before = (stack.rows(handle), stack.keys())
        assert before[0]["state"] == "created"
        again = stack.step("PUT", f"/v1/uploads/{handle}", data=CLIP)
        assert again["status"] == 204, again
    else:
        assert stack.step("PUT", f"/v1/uploads/{handle}", data=CLIP)["status"] == 204
        stack.step("POST", f"/v1/uploads/{handle}/complete", die_after=crash, died=True)
        before = (stack.rows(handle), stack.keys())
        assert before[0]["state"] == ("finalized" if crash == "complete" else "created")
    done = stack.step("POST", f"/v1/uploads/{handle}/complete")
    assert done["status"] == 200, done
    after = (stack.rows(handle), stack.keys())
    assert after[0]["state"] == "finalized" and len(after[1]) == 2
    assert [key for key in after[1] if key.startswith("media/")] == [
        key for key in after[1] if key.endswith("/source")]
    print(json.dumps({"crash": crash, "before": before, "after": after}, default=str))


@needs_stack
def test_stack__os_processes_window_and_tenants(minio, tmp_path):
    """On the database clock: a foreign tenant is 404 on PUT, completion and admission
    (nothing written for it); other bytes are 409; at the window's end completion, PUT and
    admission are 410 in every new process - twice, never revived - with the bytes still
    stored. (Idempotency needs a shared job store: `test_upload_wiring.py`.)"""
    stack = Stack(minio, tmp_path)
    handle = create(stack)
    pending = create(stack)
    assert stack.step("PUT", f"/v1/uploads/{handle}", data=CLIP)["status"] == 204
    assert stack.step("PUT", f"/v1/uploads/{pending}", data=CLIP)["status"] == 204
    other = stack.step("PUT", f"/v1/uploads/{pending}", data=OTHER_CLIP)
    assert (other["status"], other["body"]["error"]["code"]) == (409, "state_conflict")
    written = stack.keys()
    for method, path, extra in (("PUT", f"/v1/uploads/{handle}", {"data": CLIP}),
                                ("POST", f"/v1/uploads/{handle}/complete", {}),
                                ("POST", "/v1/jobs", {"json": job_body(handle)})):
        foreign = stack.step(method, path, token="other", **extra)
        assert (foreign["status"], foreign["body"]["error"]["code"]) == (404, "not_found")
    assert stack.keys() == written
    assert stack.step("POST", f"/v1/uploads/{handle}/complete")["status"] == 200
    assert stack.step("POST", "/v1/jobs", json=job_body(handle))["status"] == 202
    stack.clock.advance(TTL)
    for method, path, extra in (("POST", "/v1/jobs", {"json": job_body(handle)}),
                                ("POST", f"/v1/uploads/{pending}/complete", {}),
                                ("PUT", f"/v1/uploads/{pending}", {"data": CLIP})):
        for _ in range(2):
            late = stack.step(method, path, **extra)
            assert (late["status"], late["body"]["error"]["code"]) == (410, "upload_expired")
    assert stack.rows(pending)["state"] in ("created", "expired")
    assert f"uploads/{stack.rows(pending)['org_id']}/{pending}" in stack.keys()
