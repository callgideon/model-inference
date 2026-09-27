#!/usr/bin/env python3
"""N1 against the real D7 store: the importer publishing through `PgLabDataStore` on the
task-local PostgreSQL (0001-0029, the D7 world of `tests/d/test_d7_lab_data.py`: C1's grant
to NEMO, BOTH's to OTHER). The object port is `InMemoryObjectStore`, or the task-local MinIO
when `INFRX_M_S3_ENDPOINT` is set (bucket `infrx-n1`, prefix `test/<key>/<uuid>/`, emptied after
the module; `INFRX_M_S3_LOCAL_CREDS=1` uses the E2 MinIO literals).

T2I/G8's pattern: outside the mutant runner (a PG case in a mutant copy would contend for the
harness port's lock); the failure oracles are the fake-world cases' mutants plus the recorded
fail-first runs in the N1 evidence.

    INFRX_D_TASK=n1 uv run --frozen pytest -q tests/n/imports/test_import_pg.py
    INFRX_D_TASK=n1 INFRX_M_S3_ENDPOINT=http://127.0.0.1:57515 INFRX_M_S3_LOCAL_CREDS=1 \
        uv run --frozen pytest -q tests/n/imports/test_import_pg.py
"""
from __future__ import annotations

import os
import uuid

import pytest
from infrx.contracts import errors
from infrx.datasets import imports
from infrx.media.store import InMemoryObjectStore
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from .world import NEMO, Crash, chunks, fixture, run

# Only on an explicit task-local key: the D harness default is another lane's port.
_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") else \
    "PostgreSQL only on an explicit task-local key (INFRX_D_TASK=n1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_n1"


def object_store():
    endpoint = os.environ.get("INFRX_M_S3_ENDPOINT", "")
    if not endpoint:
        return InMemoryObjectStore()
    from infrx.media.s3 import S3ObjectStore
    if os.environ.get("INFRX_M_S3_LOCAL_CREDS") == "1":     # the E2 MinIO literals only
        os.environ.update(AWS_ACCESS_KEY_ID="infrxe2minio",
                          AWS_SECRET_ACCESS_KEY="infrx-e2-local-secret")
        os.environ.pop("AWS_SESSION_TOKEN", None)
    return S3ObjectStore.connect(os.environ.get("INFRX_M_S3_BUCKET", "infrx-n1"),
                                 f"test/{os.environ['INFRX_D_TASK']}/{uuid.uuid4()}/", endpoint)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    objects = object_store()
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        yield conn, PgLabDataStore(connector(pgharness.dsn(DB))), objects
    if not isinstance(objects, InMemoryObjectStore):
        for key in run(objects.keys("")):
            run(objects.delete(key))


def spec(name: str, **over) -> tuple[dict, bytes]:
    payload, data = fixture(name)
    return {**payload, "grant_ref": d7.W["grant"], **over}, data


def go(store, objects, payload, data, **kw):
    return run(imports.Importer(store, objects, chunk_rows=2).run(
        payload, chunks(data), provider_org_id=NEMO, actor="dev@nemo", **kw))


def count(conn, sql, *params) -> int:
    return conn.execute(sql, params).fetchone()[0]


def test_n1_pg_a_benchmark_publishes_once_with_its_splits_and_replays(world) -> None:
    """The manifest D7 stores is the importer's: five samples in the declared splits, each
    bound to the registered source and its grant, all accessible for provider_sharing; the
    same upload again is the same ref and no new row; changed bytes are a conflict."""
    conn, store, objects = world
    payload, data = spec("benchmark")
    report = go(store, objects, payload, data)
    assert report.accepted == 5 and report.rejected == []
    rows = conn.execute("select split, count(*) from infrx.lab_dataset_samples where "
                        "dataset_ref = %s group by split order by split",
                        (report.dataset_ref,)).fetchall()
    assert rows == [("holdout", 2), ("train", 2), ("validation", 1)], rows
    assert count(conn, "select count(*) from infrx.lab_sources where ref = %s",
                 report.source_ref) == 1
    assert len(run(store.accessible_samples(report.dataset_ref, provider_org_id=NEMO,
                                            purpose="provider_sharing"))) == 5
    assert go(store, objects, payload, data) == report
    assert count(conn, "select count(*) from infrx.lab_records where object_id = %s",
                 payload["dataset_id"]) == 1
    with pytest.raises(errors.Conflict):
        go(store, objects, payload, data.replace(b"forklift", b"tractor!"))


def test_n1_pg_a_video_export_and_a_crash_before_the_manifest(world) -> None:
    """A SAM-style export whose publish dies after its source registered leaves no dataset;
    the rerun registers the same source (D7 replays it) and publishes once, with each
    sample's duration."""
    conn, store, objects = world
    payload, data = spec("sam_export")
    for path, clip in (("clips/dock-01.mp4", b"dock"), ("clips/yard-07.mp4", b"yard")):
        run(objects.put_if_absent(imports.bundle_key(NEMO, payload["import_id"], path), clip,
                                  "video/mp4"))

    class Dies(PgLabDataStore):
        async def publish(self, *a, **k):
            raise Crash("killed before the manifest")
    with pytest.raises(Crash):
        go(Dies(connector(pgharness.dsn(DB))), objects, payload, data)
    assert count(conn, "select count(*) from infrx.lab_records where object_id = %s",
                 payload["dataset_id"]) == 0
    report = go(store, objects, payload, data)
    got = conn.execute("select count(*) from infrx.lab_sources where source_id = %s",
                       (payload["import_id"],)).fetchone()[0]
    assert got == 1 and report.accepted == 3
    durations = sorted(s.duration_ms for s in run(store.resolve(
        report.dataset_ref, provider_org_id=NEMO)).samples)
    assert durations == [2750, 8500, 82000], durations


def test_n1_pg_foreign_and_revoked_grants_register_nothing(world) -> None:
    """DATA-RIGHTS on the real store: another provider's grant id under NEMO's segment is
    `not_found`; after C1 revokes its grant a new import registers no source."""
    conn, store, objects = world
    foreign = d7.W["other_grant"].replace(d7.OTHER, NEMO)
    payload, data = spec("benchmark", grant_ref=foreign,
                         import_id="1a000000-0000-4000-8000-0000000000b1")
    with pytest.raises(errors.NotFound):
        go(store, objects, payload, data)
    l2.ok(conn, "lab_revoke_access_grant", {"actor_user_id": l2.C1,
                                            "grantor_org_id": l2.org(conn, l2.C1),
                                            "recipient_provider_org_id": NEMO})
    payload, data = spec("benchmark", import_id="1a000000-0000-4000-8000-0000000000b2",
                         dataset_id="da000000-0000-4000-8000-0000000000b2")
    with pytest.raises(errors.Forbidden):
        go(store, objects, payload, data)
    assert count(conn, "select count(*) from infrx.lab_sources where source_id in (%s, %s)",
                 "1a000000-0000-4000-8000-0000000000b1",
                 "1a000000-0000-4000-8000-0000000000b2") == 0
