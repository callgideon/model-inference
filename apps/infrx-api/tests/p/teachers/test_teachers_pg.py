#!/usr/bin/env python3
"""P2 on the real stores of its task-local key `p2` (WR-P2-D8): D8's `PgTeacherLedger` (0042:
teacher runs on J2's ledger, per-sample consent snapshotted, the per-item failure log) with
`lab_submission` on and a PROVIDER_USD budget for the provider's own payer; D7's
`PgLabDataStore` (an N2-imported version whose grant is current for external_judging AND
training); L2's `PgAccessStore`; P1's `import_labels` over D8's `PgLabelLog`; J2's
`HttpJudgeProvider` to the local teacher fake on 57529.

T2I/G8's pattern: outside the mutant runner; the oracles are the fake-world cases' mutants.

    INFRX_D_TASK=p2 uv run --frozen pytest -q tests/p/teachers/test_teachers_pg.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from infrx.datasets import imports
from infrx.judge.submit import HttpJudgeProvider
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import annotations as p1
from infrx.pipelines.teachers import TeacherBatch, TeacherWiring, collect, run_batch
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_data import PgLabDataStore
from infrx.state.lab_pipeline import PgLabelLog, PgTeacherLedger

from tests.j import fakes as j1
from tests.j.submit import fakes as j2
from tests.j.submit.judge_fake import JudgeFake

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from ...n.imports.world import NEMO, chunks, fixture, run
from ...n.versions.test_versions import uid
from ..annotations.world import RUBRIC, rows
from . import fakes
from .test_teachers import CEILINGS, LIVE, TEACHER_PORT

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "p2" else \
    "real services only on p2's task-local key (INFRX_D_TASK=p2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local services unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_p2"
PAYER = j2.payer(NEMO)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        l2.ok(conn, "lab_put_access_grant", l2.scope(      # C1's grant, now also external
            conn, purposes=["provider_sharing", "training", "external_judging"]))
        conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                     "values ('lab_submission', true, 'p2-test', 'teacher runs reserve')")
        d7.ok(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": PAYER,
                                       "limit": "100.00000000", "actor": "ops",
                                       "reason": "p2-test"})
        connect = connector(pgharness.dsn(DB))
        store, objects = PgLabDataStore(connect), InMemoryObjectStore()
        spec, _ = fixture("benchmark")
        spec = {**spec, "import_id": uid(1, 0x2c), "dataset_id": uid(1, 0x2d),
                "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g",
                                                       "split": "split"}}
        data = b"".join(json.dumps(i).encode() + b"\n"
                        for i in rows(6, splits=("train", "holdout", "validation")))
        ref = run(imports.Importer(store, objects).run(
            spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
        fake = JudgeFake(port=TEACHER_PORT)
        try:
            wiring = TeacherWiring(
                members=PgAccessStore(connect), ledger=PgTeacherLedger(connect),
                provider=HttpJudgeProvider(fake.url), store=store, objects=objects,
                labels=p1.import_labels, log=PgLabelLog(connect), rates=j1.TEST_RATES,
                settings=LIVE, redact=fakes.redact)
            batch = TeacherBatch(
                batch_id=str(uuid.UUID(int=0x2b, version=4)), provider_org_id=NEMO,
                requested_by=l2.DEV, dataset_ref=ref, rubric_ref=RUBRIC,
                teacher_model=j1.JUDGE_MODEL, prompt_version="teach-v1", payer_ref=PAYER,
                chunk_size=2, ceilings=CEILINGS)
            yield conn, fake, wiring, batch
        finally:
            fake.close()


def test_p2_pg_a_batch_carries_to_labels_failures_and_one_settlement(world) -> None:
    """The 'carry' case (1-JLW4-1): every non-holdout chunk is a D8 teacher run reserved on the
    payer and sent once; its collection imports the good label as a synthetic D7 annotation,
    records each per-item failure once in D8's log, and settles the run once."""
    conn, fake, wiring, batch = world
    report = run(run_batch(batch, wiring=wiring))
    assert [r.state for r in report.runs] == ["submitted"] * 2 and report.stopped is None
    assert len(fake.posts) == 2
    assert conn.execute("select purpose, count(*) from infrx.lab_judge_runs group by purpose"
                        ).fetchall() == [("teacher_annotation", 2)]
    first = report.runs[0]
    sent = sorted(first.sent_ids)
    fake.outputs[first.external_id] = [[sent[0], '{"label": "a"}'], [sent[1], "not json"],
                                       [sent[0], '{"label": "b"}']]
    got = run(collect(batch, first.run_id, wiring=wiring))
    assert got.run.state == "completed" and len(got.imported.accepted) == 1
    assert sorted(run(wiring.ledger.failures(first.run_id))) == sorted(
        [(sent[1], "malformed_label"), (sent[0], "duplicate")])
    (record,) = [run(wiring.store.resolve(r, provider_org_id=NEMO))
                 for r in got.imported.accepted]
    assert (record.method, record.ground_truth) == ("synthetic", False)
    again = run(collect(batch, first.run_id, wiring=wiring))
    assert again.run == got.run and len(run(wiring.ledger.failures(first.run_id))) == 2
    assert conn.execute("select state from infrx.lab_judge_runs where "
                        "run_id = %s", (first.run_id,)).fetchone()[0] == "completed"
