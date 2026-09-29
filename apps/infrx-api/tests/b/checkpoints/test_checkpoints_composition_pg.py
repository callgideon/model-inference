#!/usr/bin/env python3
"""WR-B3-D8-C on real PostgreSQL: the checkpoints role (`python -m infrx.lab.workers
checkpoints`, composed from `LAB_DATABASE_URL`) decides a received checkpoint over D8's
`PgCheckpointLedger` (0042), D7 and L2's real `PgAccessStore` - one relay pass, one queued
run per subscription, the receipt `evaluated`, the event acknowledged. The registry adapter
and L3's dev deployer are B3's fakes (WR-B3-3: the role refuses without them in production).
World: `test_checkpoints_pg.py`'s. Runs on the b3 key, or on p3's PostgreSQL when a foreign
`infrx-b3-postgres` holds b3 (recorded in the evidence).

T2I/G8's pattern: outside the mutant runner; the fake-level oracles are
`tests/w/test_lab_workers.py`'s checkpoints cases and their `lw_checkpoints_*` mutants.

    INFRX_D_TASK=b3 uv run --frozen pytest -q tests/b/checkpoints/test_checkpoints_composition_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.contracts import errors
from infrx.evaluation import checkpoints
from infrx.lab.access import LabAccess
from infrx.lab.workers import __main__ as lab_workers
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_pipeline import PgCheckpointLedger

from ...d import pgharness
from ...d import test_l2sql_access as l2
from .test_checkpoints_pg import DB, event, on_pg, receive, rows, run, world  # noqa: F401

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("b3", "p3") else \
    "PostgreSQL only on the b3 (or, b3 held, p3) task-local key"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")


def pumped(pump) -> None:
    """One relay pass. The queued runs' own `eval_run` events are the eval role's: this role
    refuses them after acknowledging its own (the relay raises the first such refusal last;
    a `kinds` filter is WR-LSQ-C2B's)."""
    try:
        run(pump())
    except errors.InvalidRequest as other:
        assert "eval_run" in str(other), other


def test_b3_pg_the_composed_checkpoints_role_decides_on_d8s_ledger(world, monkeypatch):
    w = on_pg(world, 3)
    dsn = pgharness.dsn(DB)
    access = LabAccess(PgAccessStore(connector(dsn)))
    sub = w.subscription(1)
    run(checkpoints.subscribe(w.ledger, w.store, sub, access=access, user_id=l2.DEV))
    e = event(w, 1)
    receive(w, e)
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])
    worker = lab_workers.compose("checkpoints", {"LAB_DATABASE_URL": dsn,
                                                 "LAB_WORKER_HEALTH_PORT": "9"},
                                 registries={"mem": w.registry.fetch}, deployer=w.deployer)
    worker.tasks["lab_checkpoints"]().close()
    pump = steps["lab checkpoints"]
    assert type(pump.__self__.scheduler.ledger) is PgCheckpointLedger
    pumped(pump)
    decided = rows(w, "select state from infrx.lab_checkpoint_decisions where checkpoint_id = "
                      "%s", e["checkpoint_id"])
    assert decided == [("queued",)], decided
    assert rows(w, "select state from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
                e["checkpoint_id"]) == [("evaluated",)]
    pumped(pump)                                           # a second pass decides nothing new
    assert rows(w, "select count(*) from infrx.lab_checkpoint_decisions where checkpoint_id = "
                   "%s", e["checkpoint_id"]) == [(1,)]
