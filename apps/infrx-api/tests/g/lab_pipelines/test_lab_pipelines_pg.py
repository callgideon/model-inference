#!/usr/bin/env python3
"""WR-P4-1's real half: `/lab/v1/pipelines` over the merged real stores - D7's `PgLabDataStore`
(labels and the external run are `infrx.lab_records` rows) and L2's `PgAccessStore` (the actor
and P1's reviewer roles on the database clock) - on the task-local PostgreSQL of key p1 (D7's
seeded world, P1's PG world). D8's label log and run ledger are not merged: they stay this
suite's fakes (SR-P1-1, SR-P3-1). Outside the mutant runner (P1's PG pattern); the oracles are
the fake-world cases' mutants.

    INFRX_D_TASK=p1 uv run --frozen pytest -q tests/g/lab_pipelines/test_lab_pipelines_pg.py
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts.lab import records
from infrx.datasets import imports
from infrx.gateway.routes import lab_pipelines as lp
from infrx.lab.access import LabAccess
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import training as p3
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_data import PgLabDataStore

from .. import support
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from ...n.imports.world import NEMO, chunks, fixture, run
from ...n.versions.test_versions import uid
from ...p.annotations.world import RUBRIC, FakeLabelLog, rows
from ...p.training import world as p3w
from .test_lab_pipelines import Ledger, Sessions, token

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "p1" else \
    "PostgreSQL only on the p1 task-local key (INFRX_D_TASK=p1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lab2p"
DEV, ADMIN = l2.DEV, l2.ADMIN
EXT, EXPORT = uid(1, 0xe0), uid(1, 0xe7)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        connect = connector(pgharness.dsn(DB))
        store, objects = PgLabDataStore(connect), InMemoryObjectStore()
        spec, _ = fixture("benchmark")
        spec = {**spec, "import_id": uid(2, 0x1c), "dataset_id": uid(2, 0xdc),
                "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g",
                                                       "split": "split"}}
        data = b"".join(json.dumps(i).encode() + b"\n"
                        for i in rows(8, splits=("train", "train", "holdout", "validation")))
        ref = run(imports.Importer(store, objects).run(
            spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
        x = lp.LabPipelines(Sessions((DEV, ADMIN)), LabAccess(PgAccessStore(connect)),
                            store=store, objects=objects, log=FakeLabelLog(),
                            ledger=Ledger(store))
        app = FastAPI()
        lp.register(app, support.runtime(), x)
        yield store, TestClient(app, raise_server_exceptions=False), ref


def call(c, user, method, path, body=None, query=None):
    return c.request(method, f"{lp.PIPELINES_PREFIX}/{path}",
                     params={"provider_org_id": NEMO, **(query or {})}, json=body,
                     headers={"authorization": f"Bearer {token(user)}"})


def test_lab_pipelines_pg__labels_to_a_submitted_manual_run_on_the_real_stores(world):
    """PIPELINE-LINEAGE on D7 and L2: the imported labels are D7 annotation records
    (`synthetic`, not ground truth); reviewed on L2's roles they export with lineage; the run is
    a D7 `lab.external_run.1` (training, PROVIDER_USD, the payer, the manual bundle) whose
    export pin is that export's digest; submitted, it reserves nothing."""
    store, c, ref = world
    train = sorted(run(store.resolve(ref, provider_org_id=NEMO)).splits.train)
    lines = "\n".join(json.dumps({"sample_id": s, "method": "model", "method_version": "v",
                                  "label": {"answer": f"a{i}"}}) for i, s in enumerate(train))
    imported = call(c, DEV, "POST", "label-imports", {
        "import_id": uid(1, 0x1b), "dataset_ref": ref, "rubric_ref": RUBRIC, "rows": lines})
    assert (imported.status_code, imported.json()["accepted"], imported.json()["rejected"]) \
        == (201, len(train), [])
    labels = call(c, DEV, "GET", "labels", query={"dataset_ref": ref}).json()["data"]
    for label in labels:
        record = run(store.resolve(label["annotation_ref"], provider_org_id=NEMO))
        assert (record.method, record.ground_truth, label["method"]) \
            == ("synthetic", False, "synthetic")
        assert call(c, ADMIN, "POST", "assignments", {
            "dataset_ref": ref, "sample_id": label["sample_id"], "reviewer_id": DEV,
            "rubric_ref": RUBRIC}).status_code == 200
        assert call(c, DEV, "POST", "reviews", {
            "dataset_ref": ref, "annotation_ref": label["annotation_ref"],
            "decision": "accepted", "rubric_ref": RUBRIC, "correction": None}).status_code == 200
    exported = call(c, DEV, "POST", "label-exports", {
        "export_id": EXPORT, "dataset_ref": ref, "adapter": "sft.1", "ttl_s": 3600}).json()
    assert [x["sample_id"] for x in exported["lineage"]] == train
    prepared = call(c, DEV, "POST", "training-runs", {
        "external_run_id": EXT, "dataset_ref": ref,
        "export": {"format": "infrx.label_export.1", "export_id": EXPORT},
        "config": {"objective": "sft", "adaptation": "lora", "base_model": "marlin-2b"},
        "payer_ref": p3w.PAYER, "limit": "25.00000000"})
    assert prepared.status_code == 201, prepared.json()
    run_record = run(store.resolve(prepared.json()["run_ref"], provider_org_id=NEMO))
    assert (run_record.purpose, run_record.connector, run_record.budget.limit.unit,
            prepared.json()["export"]["sha256"]) \
        == ("training", p3.MANUAL, "PROVIDER_USD", exported["sha256"])
    assert records.REF_RE.fullmatch(prepared.json()["run_ref"]).group(3) == EXT
    submitted = call(c, DEV, "POST", f"training-runs/{EXT}/submit")
    assert (submitted.status_code, submitted.json()["state"], submitted.json()["reserved_usd"]) \
        == (200, "submitted", "0.00000000")
    assert call(c, DEV, "GET", "training-runs").json() == {"data": [submitted.json()]}
