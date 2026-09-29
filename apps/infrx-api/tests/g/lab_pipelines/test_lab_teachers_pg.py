#!/usr/bin/env python3
"""P4.b's real half: `/lab/v1/pipelines/teacher-batches` over the merged real stores on the
task-local PostgreSQL of key p2 (57528) - D7's `PgLabDataStore` (the N-imported dataset and its
per-sample rights), L2's `PgAccessStore` (the actor, the approver's role, the database clock) and
D8's `PgTeacherLedger` (0042: one PROVIDER_USD reservation per chunk on the payer's `lab_budgets`
row, the per-sample consent snapshot, the per-item failure log) - with P2 sending through J2's
`HttpJudgeProvider` to the local teacher fake on p2's port (57529). Outside the mutant runner (the
P1 PG pattern); the oracles are the fake-world cases' mutants.

    INFRX_D_TASK=p2 uv run --frozen pytest -q tests/g/lab_pipelines/test_lab_teachers_pg.py
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts.tasklocal import local_services
from infrx.datasets import imports
from infrx.gateway.routes import lab_pipelines as lp
from infrx.judge.submit import HttpJudgeProvider
from infrx.lab.access import LabAccess
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import annotations as p1
from infrx.pipelines.teachers import TeacherWiring
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_data import PgLabDataStore
from infrx.state.lab_pipeline import PgLabelLog, PgTeacherLedger

from .. import support
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d8_ledgers as d8
from ...j import fakes as j1
from ...j.submit.judge_fake import JudgeFake
from ...n.imports.world import NEMO, chunks, fixture, run
from ...n.versions.test_versions import uid
from ...p.annotations.world import RUBRIC, rows
from ...p.teachers import fakes as p2f
from .test_lab_pipelines import LIVE, Ledger, Sessions, ceiling, token

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "p2" else \
    "PostgreSQL only on the p2 task-local key (INFRX_D_TASK=p2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_labteach"
DEV, ADMIN = d8.DEV, d8.l2.ADMIN
BATCH = uid(1, 0x7b)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    fake = JudgeFake(port=local_services("p2")["teacher-fake"].host_port)
    with pgharness.connect(DB) as conn:
        d8.seed(conn)               # D7's world, lab_submission on, the payer's 100 USD, the grant
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
        members, ledger, log = PgAccessStore(connect), PgTeacherLedger(connect), PgLabelLog(connect)
        teachers = TeacherWiring(members=members, ledger=ledger,
                                 provider=HttpJudgeProvider(fake.url), store=store,
                                 objects=objects, labels=p1.import_labels, log=log,
                                 rates=j1.TEST_RATES, settings=LIVE, redact=p2f.redact)
        x = lp.LabPipelines(Sessions((DEV, ADMIN)), LabAccess(members), store=store,
                            objects=objects, log=log, ledger=Ledger(store), teachers=teachers)
        app = FastAPI()
        lp.register(app, support.runtime(), x)
        try:
            yield conn, TestClient(app, raise_server_exceptions=False), ref, fake, ledger
        finally:
            fake.close()


def call(c, user, method, path, body=None):
    return c.request(method, f"{lp.PIPELINES_PREFIX}/{path}", params={"provider_org_id": NEMO},
                     json=body, headers={"authorization": f"Bearer {token(user)}"})


def test_lab_teachers_pg__a_dry_run_then_an_approved_batch_on_the_real_ledger(world):
    """PIPELINE-BUDGET on D7, L2 and D8: the dry run reserves nothing on the payer's budget and
    sends nothing; a developer cannot approve; the administrator's approval reserves each chunk
    at its ceiling on the real `lab_budgets` row (0042's per-sample consent snapshot) and sends
    each once to the local teacher fake, redacted; a second approval sends nothing more; the
    per-item failure log reads back."""
    conn, c, ref, fake, ledger = world
    before = d8.budget(conn, d7.PAYER)
    body = {"batch_id": BATCH, "dataset_ref": ref, "rubric_ref": RUBRIC,
            "teacher_model": j1.JUDGE_MODEL, "prompt_version": "teach-v1",
            "payer_ref": d7.PAYER, "budget_usd": "1.00000000", "chunk_size": 4}
    planned = call(c, DEV, "POST", "teacher-batches", body)
    assert planned.status_code == 201, planned.json()
    assert [(x["samples"], x["state"]) for x in planned.json()["chunks"]] \
        == [(4, "unreserved"), (2, "unreserved")]
    assert (planned.json()["not_permitted"], planned.json()["holdout"]) == (0, 2)
    assert (d8.budget(conn, d7.PAYER), fake.posts) == (before, [])
    denied = call(c, DEV, "POST", f"teacher-batches/{BATCH}/approve")
    assert (denied.status_code, denied.json()) == (403, {"refusal": "denied"})
    approved = call(c, ADMIN, "POST", f"teacher-batches/{BATCH}/approve")
    assert approved.status_code == 200, approved.json()
    chunks = approved.json()["chunks"]
    assert [(x["state"], x["reserved_usd"], x["sent"]) for x in chunks] \
        == [("submitted", ceiling(4), 4), ("submitted", ceiling(2), 2)]
    assert approved.json()["approval"]["approved_by"] == ADMIN
    assert len(fake.posts) == 2 and "a@b.example" not in json.dumps(fake.posts)
    reserved = d8.budget(conn, d7.PAYER)[0]
    assert float(reserved) - float(before[0]) == pytest.approx(0.68124), (before, reserved)
    again = call(c, ADMIN, "POST", f"teacher-batches/{BATCH}/approve")
    assert (again.status_code, again.json(), len(fake.posts)) == (200, approved.json(), 2)
    run(ledger.record_failures(chunks[0]["run_id"], [(uid(1, 0x88), "malformed_label")]))
    listed = call(c, DEV, "GET", "teacher-batches").json()["data"][0]["chunks"][0]["failures"]
    assert listed == [{"sample_id": uid(1, 0x88), "reason": "malformed_label"}]
