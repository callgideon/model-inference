#!/usr/bin/env python3
"""WR-J3-D8-C on the real store of j2: the judge role's report job
(`python -m infrx.lab.workers judge`'s `jobs["judge_report"]`, composed on `LAB_DATABASE_URL`)
publishes each configuration's calibration through D8's `PgJudgeLedger.put_calibration`
(0043), and the Lab's session door `public.lab_judge_runs` shows it to the provider's own
developer - the world of `test_calibration_pg.py` (D8's requests world, one judge run of NEMO
that SENT the request). The trace stack is never reached (T3's retention is recorded).

T2I/G8's pattern: outside the mutant runner; the fake-level oracle is
`tests/w/test_lab_workers.py::test_lab_workers__the_judge_report_job_publishes_each_...`
and its `lw_report_*` mutants.

    INFRX_D_TASK=j2 uv run --frozen pytest -q tests/j/calibration/test_calibration_composition_pg.py
"""
from __future__ import annotations

import asyncio

from infrx.judge.calibration import MIN_PAIRS
from infrx.lab.workers import __main__ as lab_workers

from ...d import pgharness
from ...d import test_d8_requests as q
from .test_calibration import agreeing
from .test_calibration_pg import DB, conn, evidence, pytestmark  # noqa: F401 - the j2 world


def test_j3_pg_the_judge_roles_report_job_feeds_the_door(conn, monkeypatch) -> None:
    monkeypatch.setattr(lab_workers, "trace_retention", lambda limits, url: object())
    worker = lab_workers.compose("judge", {
        "LAB_DATABASE_URL": pgharness.dsn(DB), "LAB_WORKER_HEALTH_PORT": "9",
        "JUDGE_PROVIDER_URL": "http://127.0.0.1:9", "CLICKHOUSE_URL": "http://ch.invalid:8123/x",
        "S3_TRACE_BUCKET": "unused"})
    q.judged(conn, q.uid(9, 0x7f), result=q.SCORES)
    grantor = q.l2.org(conn, q.C1)
    results, feedback = evidence(grantor, agreeing(MIN_PAIRS))
    done = asyncio.run(worker.jobs["judge_report"]([
        {"results": results, "feedback": feedback, "provider_org_id": q.NEMO,
         "org_id": grantor, "judge_model": "judge-1", "rubric_version": 1}]))
    assert done == {"published": 1, "failed": 0}
    state, runs = q.door(conn, q.DEV)
    assert state is None and {r["calibration"]["state"] for r in runs} == {"calibrated"}
    assert q.door(conn, q.VIEWER)[0] == "42501"
