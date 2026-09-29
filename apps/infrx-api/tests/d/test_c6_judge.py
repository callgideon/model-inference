#!/usr/bin/env python3
"""Composition batch 6 on D6J's world: `0053`'s provider listing for the judge role's pass
(WR-C5-PROVIDERS) - the providers with judge runs in the states the pass works - on real
PostgreSQL, composed with `PgJudgeLedger`. Each `check_*` is the check a mutant in
`code_mutants_c6.py` (`JUDGE`) must break.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/d/test_c6_judge.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_consent import PgJudgeLedger

from . import pgharness
from . import test_d6j_judge as j

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_c6j"
NEMO, OTHER, uid, ok, call = j.NEMO, j.OTHER, j.uid, j.ok, j.call
seed = j.seed


def check_providers_with_judge_work_are_listed_by_state(conn) -> str:
    """WR-C5-PROVIDERS: the providers with a judge run in one of the given states (NEMO's
    submitted run), never one whose runs are all elsewhere (OTHER's only run is prepared).
    Commits (the store reads on its own connection)."""
    j.submitted(conn, uid(0x61))
    with conn.transaction():
        call(conn, "lab_judge_reserve", j.reserve(uid(0x62), payer=j.OTHER_PAYER,
                                                  provider=OTHER, grant=j.W["other_grant"]))
    work = ok(conn, "lab_providers_with", {"work": "judge",
                                          "states": ["ambiguous", "submitted"]})
    assert work == [NEMO], work
    prepared = ok(conn, "lab_providers_with", {"work": "judge", "states": ["prepared"]})
    assert OTHER in prepared, prepared
    store = PgJudgeLedger(connector(pgharness.dsn(conn.info.dbname)))
    assert asyncio.run(store.providers_in(("ambiguous", "submitted"))) == [NEMO]
    return "the providers with judge work in the pass's states only; the ledger reads it"


CHECKS = {c.__name__: c for c in (check_providers_with_judge_work_are_listed_by_state,)}


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_c6_judge(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
