#!/usr/bin/env python3
"""WR-P2-D8-C on the real stores of p2: the Lab worker composition's `teacher_wiring`
(`infrx.lab.workers.__main__`) over the p2 world of `test_teachers_pg.py` (D8's 0042 teacher
runs with `lab_submission` on and the provider's own PROVIDER_USD payer budget, D7, L2, P1's
import over D8's label log, J2's provider to the local teacher fake on 57529). The failure
oracle: a composition with a plain `PgJudgeLedger` dies with AttributeError
(`record_failures`) at the first per-item failure, so the run never settles.

T2I/G8's pattern: outside the mutant runner; the fake-level oracle is
`tests/w/test_lab_workers.py::test_lab_workers__the_teacher_wiring_is_p2_on_d8s_teacher_ledger`
and its `lw_teacher_*` mutants.

    INFRX_D_TASK=p2 uv run --frozen pytest -q tests/p/teachers/test_teachers_composition_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

from infrx.lab.workers import __main__ as lab_workers
from infrx.pipelines.teachers import collect, run_batch

from tests.j import fakes as j1

from ...n.imports.world import run
from . import fakes
from .test_teachers import LIVE
from .test_teachers_pg import pytestmark, world  # noqa: F401 - the p2 world and its skip


def test_p2_pg_the_composed_teacher_wiring_records_failures_and_settles_once(world) -> None:
    conn, fake, given, batch = world
    wiring = lab_workers.teacher_wiring(given.store._connect, given.objects,
                                        provider_url=fake.url, settings=LIVE,
                                        redact=fakes.redact, rates=j1.TEST_RATES)
    report = run(run_batch(batch, wiring=wiring))
    assert [r.state for r in report.runs] == ["submitted"] * 2 and report.stopped is None
    first = report.runs[0]
    sent = sorted(first.sent_ids)
    fake.outputs[first.external_id] = [[sent[0], '{"label": "a"}'], [sent[1], "not json"]]
    got = run(collect(batch, first.run_id, wiring=wiring))
    assert got.run.state == "completed" and len(got.imported.accepted) == 1
    assert run(wiring.ledger.failures(first.run_id)) == [(sent[1], "malformed_label")]
    assert conn.execute("select count(*) from infrx.lab_label_events").fetchone()[0] >= 1
    assert run(collect(batch, first.run_id, wiring=wiring)).run == got.run
    assert conn.execute("select state from infrx.lab_judge_runs where run_id = %s",
                        (first.run_id,)).fetchone()[0] == "completed"
