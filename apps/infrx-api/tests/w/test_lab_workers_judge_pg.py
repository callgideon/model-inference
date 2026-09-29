#!/usr/bin/env python3
"""WR-LSQ-C2A's real half: the judge role's collect/reconcile pass (`judge_pass`, as
`python -m infrx.lab.workers judge` schedules it) over D6J's `PgJudgeLedger` (0036 + 0049's
`runs_in`) and the providers read on the role's login, in J2's PostgreSQL world
(`tests/j/submit`, INFRX_D_TASK=j2, 57511); J2's provider is its in-process fake.

A run whose answer was lost (the provider HAS the batch) is reconciled from the provider's
record and collected in the same pass (results stored once, settled once); a run the provider
never took stays `ambiguous` with its hold (R184/R192: never released or resent by the
platform); a second pass changes nothing. Outside the mutant runner; the oracles are
`test_lab_workers.py`'s judge pass case and its `lw_judge_pass_*` mutants.

    INFRX_D_TASK=j2 uv run --frozen pytest -q tests/w/test_lab_workers_judge_pg.py
"""
# ruff: noqa: F811 - the pytest fixtures imported from J2's conftest are the parameters
from __future__ import annotations

import asyncio
import json
import os
from functools import partial

import pytest
from infrx.lab.workers import __main__ as lab_workers
from infrx.state.jobstore import connector

from ..d import pgharness
from ..j import fakes as j1
from ..j.submit.conftest import CASE, pg_template, pg_world  # noqa: F401
from ..j.submit.test_submit import PER_RUN
from ..j.submit.test_submit_ledgers import Judging

pytestmark = pytest.mark.skipif(os.environ.get("INFRX_D_TASK") != "j2",
                                reason="PostgreSQL only on the j2 task-local key")


def test_lab_workers_judge_pg__the_pass_reconciles_what_the_provider_has_and_collects_it(
        pg_world):
    case = Judging(pg_world, True, mode="lost")
    lost = asyncio.run(case.submit())
    case.provider.mode = "timeout"
    never = asyncio.run(case.submit(case.job(2)))
    assert (lost.state, never.state) == ("ambiguous", "ambiguous")
    case.provider.outputs["batch-1"] = [(s, json.dumps(j1.result())) for s in case.ids]
    providers = partial(lab_workers.provider_ids, connector(pgharness.dsn(CASE)))
    first = asyncio.run(lab_workers.judge_pass(case.wiring, providers))
    assert first == {"reconciled": 1, "waiting": 1, "collected": 1, "failed": 0}, first
    done = asyncio.run(case.ledger.run(lost.run_id))
    assert done.state == "completed" and done.actual == case.provider.cost
    assert case.stored(lost.run_id) == sorted(case.ids)
    held = asyncio.run(case.ledger.run(never.run_id))
    assert held.state == "ambiguous", "R184: never released or resent by the platform"
    assert case.committed() == case.provider.cost + PER_RUN
    again = asyncio.run(lab_workers.judge_pass(case.wiring, providers))
    assert again == {"reconciled": 0, "waiting": 1, "collected": 0, "failed": 0}, again
    assert len(case.provider.calls) == 2 and case.stored(lost.run_id) == sorted(case.ids)
