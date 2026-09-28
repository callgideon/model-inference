#!/usr/bin/env python3
"""I5 (LAB-WORKERS) local drills on the i5 task-local services: PostgreSQL 57523 (0001-0029,
D7's seeded grant and source) and MinIO 57524 (`infrx-i5-s3`, started and removed here).
The workers are B1's `Runner` driven as the eval role's entry point will drive it (WR-B-5):
a lease loop over the real D7 store.

* a worker killed with an active lease: recovery puts the case back, a second worker ends the
  run, and the killed worker's late finish is refused (no stale commit);
* backup/restore: `pg_dump -Fc` of the Lab database mid-run, `pg_restore` into a fresh one,
  the run resumes there and ends with every case scored once;
* object store lost: every case fails `missing_content` with no paid call (bounded, visible);
  restored from the backup copy, a rerun scores every case;
* a revoked dev credential (401): each case fails once, no retry.
Outside the mutant runner (N2/T2I's pattern for PG drills).

    INFRX_D_TASK=i5 uv run --frozen pytest -q tests/i/lab_eval/test_drills_pg.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid

import httpx
import pytest
from infrx.contracts import errors
from infrx.contracts.tasklocal import local_services
from infrx.datasets.imports import sample_key
from infrx.evaluation import runner
from infrx.evaluation.runner import HttpDevEndpoint, Limits, Runner
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from ...b.runner.world import (DEPLOYMENT, DEV, NEMO, RATE_CARD, SPEC, Crash, DevWallet,
                               access, content, eval_run, harness, manifest, uid)
from ...d import pgharness
from ...d import test_d7_lab_data as d7

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "i5" else \
    "PostgreSQL only on the i5 task-local key (INFRX_D_TASK=i5)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB, RESTORED = f"{pgharness.DATABASE}_drill", f"{pgharness.DATABASE}_restored"
S3 = local_services("i5")["s3"]
MINIO = "pgsty/minio@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372"
S3_USER, S3_SECRET, BUCKET = "infrxi5minio", "infrx-i5-local-secret", "infrx-i5"  # test-only
LIMITS = Limits(lease_s=30, max_attempts=3, dispatch_retries=1, concurrency=1)
N = 3


def run(coro):
    return asyncio.run(coro)


def docker(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), input=stdin, capture_output=True, check=True)


@pytest.fixture(scope="module")
def pg():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        yield conn, PgLabDataStore(connector(pgharness.dsn(DB)))


@pytest.fixture(scope="module")
def s3():
    """MinIO on the i5 port, this module's only; removed at the end."""
    # P-21 is not extended yet: 57524 sits in the kernel's ephemeral range, and another
    # lane's client socket may hold it in TIME-WAIT (seen 2026-09-27) - wait that out.
    for _ in range(12):
        docker("rm", "-f", "-v", S3.container)
        try:
            docker("run", "-d", "--name", S3.container, "-p", f"127.0.0.1:{S3.host_port}:9000",
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
        objects = S3ObjectStore.connect(BUCKET, f"{S3.object_prefix}{uuid.uuid4().hex}/",
                                        f"http://127.0.0.1:{S3.host_port}")
        for _ in range(60):
            try:
                objects.client.create_bucket(Bucket=BUCKET)
                break
            except Exception:                  # MinIO still starting
                time.sleep(0.5)
        yield objects
    finally:
        docker("rm", "-f", "-v", S3.container)


class Case:
    """One run of N samples over the seeded grant; content objects in `objects`."""

    def __init__(self, conn, store, objects, n: int) -> None:
        self.conn, self.store, self.objects = conn, store, objects
        self.manifest = manifest(N, d7.W["grant"], d7.W["source"], dataset=0x50 + n)
        self.ids = sorted(s["sample_id"] for s in self.manifest["samples"])
        self.put()
        dataset = run(store.publish(self.manifest, provider_org_id=NEMO, actor=DEV))
        harness_ref = run(store.publish(harness(harness_id=uid(0x50 + n, 0xa7)),
                                        provider_org_id=NEMO, actor=DEV))
        self.payload = eval_run(dataset, harness_ref, run=0x50 + n)
        self.frozen = self.freeze()
        self.wallet = DevWallet("1000")

    def put(self) -> None:
        for i, sample in enumerate(self.manifest["samples"], 1):
            run(self.objects.put_if_absent(sample_key(NEMO, sample["content_digest"]),
                                           content(i), "application/json"))

    def freeze(self, payload=None):
        return run(runner.freeze(self.store, payload or self.payload, evaluator=SPEC,
                                 access=access(), user_id=DEV, provider_org_id=NEMO))

    def runner(self, worker: str = "w", endpoint=None, store=None) -> Runner:
        return Runner(store or self.store, self.objects, endpoint or self.wallet, DEPLOYMENT,
                      worker_id=worker, limits=LIMITS)

    def results(self, conn=None, run_id=None) -> int:
        return (conn or self.conn).execute(
            "select count(*) from infrx.lab_eval_results where run_id = %s",
            (run_id or self.frozen.run.run_id,)).fetchone()[0]


def test_i5_pg_a_killed_worker_never_commits_stale_and_its_case_is_recovered(pg) -> None:
    from infrx.media.store import InMemoryObjectStore
    conn, store = pg
    c = Case(conn, store, InMemoryObjectStore(), 1)
    stale = run(store.lease_case(c.frozen.run.run_id, provider_org_id=NEMO,
                                 worker_id="killed", lease_s=30))
    d7.advance(conn, 31)                        # the killed worker's lease runs out
    assert run(store.recover()) == 1
    report = run(c.runner("w2").run(c.frozen))
    assert report["cases"] == {"done": N} and c.results() == N
    with pytest.raises(errors.StaleLease):
        run(store.finish(stale, outcome="succeeded", results=[
            {"evaluator_ref": c.frozen.run.evaluator_ref, "body": "{}"}]))
    assert c.results() == N and len(c.wallet.calls) == N


def test_i5_pg_a_backup_taken_mid_run_restores_and_the_run_resumes_there(pg) -> None:
    from infrx.media.store import InMemoryObjectStore
    conn, store = pg
    c = Case(conn, store, InMemoryObjectStore(), 2)
    c.wallet.crash_after_charge = True
    with pytest.raises(Crash):                  # one case charged, the worker died
        run(c.runner().run(c.frozen))
    dump = docker("exec", pgharness.CONTAINER, "pg_dump", "-U", "postgres", "-Fc",
                  "-d", DB).stdout
    pgharness.recreate(RESTORED)
    subprocess.run(("docker", "exec", "-i", pgharness.CONTAINER, "pg_restore", "-U",
                    "postgres", "--no-owner", "--exit-on-error", "-d", RESTORED),
                   input=dump, capture_output=True, check=True)
    restored = PgLabDataStore(connector(pgharness.dsn(RESTORED)))
    with pgharness.connect(RESTORED) as copy:
        d7.advance(copy, 31)
        assert run(restored.recover()) == 1
        again = run(runner.resume(restored, c.frozen.run.run_id, evaluator=SPEC,
                                  provider_org_id=NEMO))
        assert again == c.frozen
        c.wallet.crash_after_charge = False
        report = run(c.runner("w2", store=restored).run(again))
        assert report["cases"] == {"done": N} and c.results(copy) == N
        assert copy.execute("select count(*) from infrx.lab_eval_attempts where run_id = %s "
                            "and state = 'expired'", (again.run.run_id,)).fetchone()[0] == 1
    assert c.results() == 0                     # the original is untouched


def test_i5_pg_a_lost_object_store_fails_visibly_without_paid_calls_and_restores(pg, s3) -> None:
    conn, store = pg
    c = Case(conn, store, s3, 3)
    backup = {key: run(s3.get(key)) for key in run(s3.keys(""))}
    assert len(backup) == N
    for key in backup:                          # the store is lost
        run(s3.delete(key))
    report = run(c.runner().run(c.frozen))
    assert report["failures"] == {i: "missing_content" for i in c.ids}
    assert c.wallet.calls == [] and report["cases"] == {"failed": N}
    for key, data in backup.items():            # restored from the backup copy
        run(s3.put_if_absent(key, data, "application/json"))
    rerun = c.freeze(eval_run(c.payload["dataset_ref"], c.payload["harness_ref"], run=0x60))
    report = run(c.runner().run(rerun))
    assert report["cases"] == {"done": N} and c.results(run_id=rerun.run.run_id) == N


def test_i5_pg_a_revoked_dev_credential_fails_each_case_once_without_retry(pg) -> None:
    from infrx.media.store import InMemoryObjectStore
    conn, store = pg
    c = Case(conn, store, InMemoryObjectStore(), 4)
    seen = []

    def refuse(request):
        seen.append(request)
        return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})

    endpoint = HttpDevEndpoint("http://dev", api_key="revoked", model="m", rate_card=RATE_CARD,
                               client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    report = run(c.runner(endpoint=endpoint).run(c.frozen))
    assert report["failures"] == {i: "invalid_request" for i in c.ids}
    assert len(seen) == N and report["cases"] == {"failed": N}
