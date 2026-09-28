#!/usr/bin/env python3
"""J3 on the real stores of its task-local key `j2` (WR-J3-D8): the report job stores its
calibration with D8's `PgJudgeLedger.put_calibration` (0043), and the Lab's session door
`public.lab_judge_runs` (WR-V3-1's read) shows it on the provider's own Lab-requested run,
through the role matrix: developer and administrator see their provider's run; a viewer, a
consumer, another provider's developer and anon are refused (42501); another provider's
developer sees none of this provider's runs on their own provider. World: D8's requests
world (`tests/d/test_d8_requests.py`), one judge run of NEMO that SENT the request.

T2I/G8's pattern: outside the mutant runner; the oracles are the fake-world cases' mutants.

    INFRX_D_TASK=j2 uv run --frozen pytest -q tests/j/calibration/test_calibration_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.judge.calibration import MIN_PAIRS, publish
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_consent import PgJudgeLedger

from tests.j import fakes

from ...d import pgharness
from ...d import test_d8_requests as q
from .test_calibration import BAD, C, agreeing, judged, sid

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "j2" else \
    "PostgreSQL only on the j2 task-local key (INFRX_D_TASK=j2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_j3"


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        q.seed(connection)
        yield connection


def evidence(grantor: str, pairs):
    """[(n, judge pass, verdict)] -> the grantor's results and operator labels."""
    return ([judged(n, passed=ok) for n, ok, _ in pairs],
            [fakes.label(sid(n), org_id=grantor, verdict=v) for n, _, v in pairs])


def test_j3_pg_the_report_job_feeds_the_door_for_the_providers_own_developers(conn) -> None:
    q.judged(conn, q.uid(9, 0x7e), result=q.SCORES)
    grantor, ledger = q.l2.org(conn, q.C1), PgJudgeLedger(connector(pgharness.dsn(DB)))
    job = dict(provider_org_id=q.NEMO, org_id=grantor, judge_model="judge-1", rubric_version=1)
    state, (before,) = q.door(conn, q.DEV)
    assert state is None and before["calibration"] == q.UNCALIBRATED
    good = asyncio.run(publish(ledger, *evidence(grantor, agreeing(MIN_PAIRS)), **job))
    assert good.calibration()["state"] == "calibrated"
    for user in (q.DEV, q.ADMIN):                          # developer+ : own provider's run
        state, runs = q.door(conn, user)
        assert state is None and [r["calibration"] for r in runs] == [good.calibration()], user
    refused = {user: q.door(conn, user)[0] for user in (q.VIEWER, q.C1, q.BOTH, None)}
    assert refused == dict.fromkeys(refused, "42501"), refused   # viewer, consumer, foreign, anon
    assert q.door(conn, q.BOTH, q.OTHER) == (None, [])     # a foreign provider: none of NEMO's
    few = asyncio.run(publish(ledger, *evidence(grantor, [(n, True, C if n else BAD)
                                                          for n in range(3)]), **job))
    assert few.calibration()["state"] == "insufficient"
    assert [r["calibration"] for r in q.door(conn, q.DEV)[1]] == [few.calibration()]  # latest
