#!/usr/bin/env python3
"""WR-P4B-2's real half: the annotation role's pass (`python -m infrx.lab.workers annotation`'s
`collect_teachers`, composed from its environment) collecting the batch the gateway's composed
surface approved (`test_lab_teachers_composition_pg`'s world: D7, L2 and D8's
`PgTeacherLedger`/`PgLabelLog` on the p2 PostgreSQL, 57528, and J2's local teacher fake on
57529). Outside the mutant runner; the oracles are `lw_collect_*` / `lw_annotation_*`.

    INFRX_D_TASK=p2 uv run --frozen pytest -q tests/w/test_lab_workers_teachers_pg.py
"""
from __future__ import annotations

from infrx.lab.workers import __main__ as lab_workers

from ..d import pgharness
from ..g.lab_pipelines import test_lab_teachers_composition_pg as composed
from ..g.lab_pipelines import test_lab_teachers_pg as p4b
from ..g.lab_pipelines.test_lab_teachers_composition_pg import world  # noqa: F401,F811
from ..n.imports.world import run

pytestmark = composed.pytestmark


def events(conn) -> int:
    return conn.execute("select count(*) from infrx.lab_label_events").fetchone()[0]


def test_lab_workers_teachers_pg__the_annotation_role_collects_an_approved_batch_once(world):  # noqa: F811
    """The approved batch's two submitted chunks are collected by the role: the good labels
    are imported once through P1 (D8's label log), the malformed answer is D8's per-item
    failure, each run settles once at the teacher's reported cost; a second pass collects
    nothing (a completed run is never collected again)."""
    conn, c, _, fake, ledger = world
    p4b.test_lab_teachers_pg__a_dry_run_then_an_approved_batch_on_the_real_ledger(world)
    env = {"LAB_DATABASE_URL": pgharness.dsn(composed.DB), "LAB_WORKER_HEALTH_PORT": "18012",
           "LAB_S3_BUCKET": "not-read", "LAB_TEACHER_URL": fake.url}
    worker = lab_workers.compose("annotation", env,
                                 objects=c.app.state.lab.teachers.objects)
    chunks = p4b.call(c, p4b.DEV, "GET", "teacher-batches").json()["data"][0]["chunks"]
    first, second = (run(ledger.run(x["run_id"])) for x in chunks)
    assert (first.state, second.state) == ("submitted", "submitted")
    one, two = sorted(first.sent_ids), sorted(second.sent_ids)
    fake.outputs[first.external_id] = [[one[0], '{"label": "a"}'], [one[1], "not json"]]
    fake.outputs[second.external_id] = [[s, '{"label": "b"}'] for s in two]
    before = events(conn)
    assert run(lab_workers.collect_teachers(worker.wiring)) == {"collected": 2, "failed": 0}
    assert events(conn) - before == 1 + len(two)
    assert (one[1], "malformed_label") in run(ledger.failures(first.run_id))
    assert [run(ledger.run(r.run_id)).state for r in (first, second)] == ["completed"] * 2
    assert conn.execute("select sum(actual)::text from infrx.lab_judge_runs where run_id = "
                        "any(%s::uuid[])", ([first.run_id, second.run_id],)).fetchone()[0] \
        == "0.02000000"
    assert run(lab_workers.collect_teachers(worker.wiring)) == {"collected": 0, "failed": 0}
    assert events(conn) - before == 1 + len(two)
