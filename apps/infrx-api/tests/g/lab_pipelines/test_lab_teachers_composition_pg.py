#!/usr/bin/env python3
"""WR-P4B-1's real half: P4.b's teacher flow (`test_lab_teachers_pg`) over the surface the
gateway composes with `LAB_PIPELINES` and `LAB_TEACHERS` on (`pilot._lab`) - P2's
`TeacherWiring` of L2's `PgAccessStore`, D8's `PgTeacherLedger`/`PgLabelLog` and D7's store on
one connection, N2's public redaction (WR-P2-4) and J2's `HttpJudgeProvider` to the local teacher
fake `LAB_TEACHER_URL` names - on the task-local PostgreSQL of key p2 (57528, teacher fake
57529). Two things are the case's own: the session verifier (the flow's test tokens, not the
project's auth server) and the rate table (the approved one is empty until a rate is approved,
so every approval would be an unpriced 409); `JUDGE_MODE` is set live for the approval. Outside
the mutant runner; the oracles are the startup case's `lab_teachers_*` mutants.

    INFRX_D_TASK=p2 uv run --frozen pytest -q tests/g/lab_pipelines/test_lab_teachers_composition_pg.py
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts.tasklocal import local_services
from infrx.datasets import imports
from infrx.datasets.versions import redact_content
from infrx.gateway import pilot
from infrx.gateway.routes import lab_pipelines as lp
from infrx.media.store import InMemoryObjectStore
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_pipeline import PgTeacherLedger

from .. import support
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d8_ledgers as d8
from ...j import fakes as j1
from ...j.submit.judge_fake import JudgeFake
from ...n.imports.world import NEMO, chunks, fixture, run
from ...n.versions.test_versions import uid
from ...p.annotations.world import rows
from . import test_lab_teachers_pg as p4b
from .test_lab_pipelines import Sessions

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "p2" else \
    "PostgreSQL only on the p2 task-local key (INFRX_D_TASK=p2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_labteachc"


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    fake = JudgeFake(port=local_services("p2")["teacher-fake"].host_port)
    with pgharness.connect(DB) as conn:
        d8.seed(conn)
        connect, objects = connector(pgharness.dsn(DB)), InMemoryObjectStore()
        settings = support.settings(judge_mode="live", deployment=dataclasses.replace(
            support.BUILD, lab_pipelines=True, lab_teachers=True, lab_teacher_url=fake.url))
        x = pilot._lab(settings, connect, objects)["lab_pipelines"]
        assert (type(x.teachers.ledger), x.teachers.redact) == (PgTeacherLedger, redact_content)
        spec, _ = fixture("benchmark")
        spec = {**spec, "import_id": uid(2, 0x1c), "dataset_id": uid(2, 0xdc),
                "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g",
                                                       "split": "split"}}
        data = b"".join(json.dumps(i).encode() + b"\n"
                        for i in rows(8, prefix="q a@b.example",          # personal data:
                                      splits=("train", "train", "holdout", "validation")))
        ref = run(imports.Importer(x.store, objects).run(
            spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
        x = dataclasses.replace(x, sessions=Sessions((p4b.DEV, p4b.ADMIN)),
                                teachers=dataclasses.replace(x.teachers, rates=j1.TEST_RATES))
        app = FastAPI()
        lp.register(app, support.runtime(), x)
        app.state.lab = x                   # the composed surface, for the worker's twin
        try:
            yield (conn, TestClient(app, raise_server_exceptions=False), ref, fake,
                   x.teachers.ledger)
        finally:
            fake.close()


def test_lab_teachers_composition_pg__the_composed_surface_runs_the_approved_batch(world):
    """PIPELINE-BUDGET through the gateway's own composition: P4.b's flow unchanged (dry run
    reserves and sends nothing, a developer cannot approve, the administrator's approval
    reserves each chunk on the real `lab_budgets` row and sends each once, redacted, a second
    approval sends nothing, the failure log reads back) - over content carrying an email
    address that never reaches the teacher (N2's redaction, WR-P2-4)."""
    p4b.test_lab_teachers_pg__a_dry_run_then_an_approved_batch_on_the_real_ledger(world)
