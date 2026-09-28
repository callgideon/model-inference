#!/usr/bin/env python3
"""B3 on the real stores: D7's `PgLabDataStore` and D8's `PgCheckpointLedger` (0042; WR-B3-D8)
on the b3 task-local PostgreSQL (D7's seeded grant and source, the B1 evaluator registered as
D7 requires, R167). The ledger's clock is the database's (`infrx.now()`), frozen at the
world's NOW; subscription ids are per world, as both worlds share one ledger. The L2 port is
the fake (L2's own PG suite proves its store).

Drills (CHECKPOINT-IDEM): one receipt and ONE `checkpoint_received` outbox row per id, a
replay the same receipt, other bytes under the id a conflict; a valid checkpoint queues one
D7 run per subscription and ends `evaluated`, a redelivery none more; a crash between the
run's creation and its decision is one run; a malformed artifact ends `rejected` with no
run. Outside the mutant runner (the fake-world cases' mutants are the oracles).

    INFRX_D_TASK=b3 uv run --frozen pytest -q tests/b/checkpoints/test_checkpoints_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.contracts import errors
from infrx.evaluation import checkpoints
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore
from infrx.state.lab_pipeline import PgCheckpointLedger

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ..runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
from .world import DEV, NEMO, NOW, Crash, World, safetensors

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "b3" else \
    "PostgreSQL only on the b3 task-local key (INFRX_D_TASK=b3)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_ckpt"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        conn.execute("select infrx_test.freeze(%s)", (NOW,))
        store = PgLabDataStore(connector(pgharness.dsn(DB)))
        run(store.put_evaluator(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID,
                                actor=DEV))
        yield conn, store


class Ledger(PgCheckpointLedger):
    """D8's ledger with the fake's crash hook: die before recording a decision."""

    crash_on_decide = 0

    async def decide(self, *args, **kw):
        if self.crash_on_decide:
            self.crash_on_decide -= 1
            raise Crash("died before recording the decision")
        return await super().decide(*args, **kw)


def on_pg(world, n: int) -> World:
    """A fake-world `World` whose D7 is the real store, with its own dataset and run."""
    conn, store = world
    w = World()
    w.conn, w.store, w.ledger = conn, store, Ledger(connector(pgharness.dsn(DB)))
    w.dataset = run(store.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=0x30 + n),
                                  provider_org_id=NEMO, actor=DEV))
    w.harness = run(store.publish(harness(harness_id=uid(0x30 + n, 0xa7)),
                                  provider_org_id=NEMO, actor=DEV))
    w.external = run(store.publish(w.external_run(uid(0x30 + n, 0xe3)), provider_org_id=NEMO,
                                   actor=DEV))
    w.tag = n
    w.subscription = lambda k, **kw: World.subscription(w, k + 100 * n, **kw)
    return w


def event(w: World, n: int, **kw) -> dict:
    return w.event(w.tag * 100 + n, **kw)


def rows(w: World, sql: str, *params) -> list[tuple]:
    return w.conn.execute(sql, params).fetchall()


def receive(w: World, e: dict):
    body, signature = w.body(e)
    return run(checkpoints.receive(body, signature, keys=w.keys.get, ledger=w.ledger,
                                   store=w.store))


def handle(w: World, e: dict):
    return run(checkpoints.on_checkpoint(
        e["checkpoint_id"], provider_org_id=NEMO, ledger=w.ledger, store=w.store,
        registries={"mem": w.registry.fetch}, deployer=w.deployer, access=w.access))


def test_b3_pg_a_checkpoint_is_received_once_and_queues_one_run_per_subscription(world):
    w = on_pg(world, 1)
    run(checkpoints.subscribe(w.ledger, w.store, w.subscription(1), access=w.access,
                              user_id=DEV))
    run(checkpoints.subscribe(w.ledger, w.store, w.subscription(2, policy="every"),
                              access=w.access, user_id=DEV))
    e = event(w, 1)
    first = receive(w, e)
    assert first["state"] == "received" and receive(w, e) == first
    assert rows(w, "select count(*) from infrx.lab_outbox where kind = 'checkpoint_received' "
                   "and payload->>'checkpoint_id' = %s", e["checkpoint_id"]) == [(1,)]
    with pytest.raises(errors.IdempotencyConflict):
        receive(w, event(w, 1, data=safetensors(7), uri="mem://changed"))
    out = handle(w, e)
    assert {d["state"] for d in out.values()} == {"queued"} and handle(w, e) == out
    run_ids = sorted(d["run_id"] for d in out.values())
    assert rows(w, "select run_id::text from infrx.lab_eval_runs where run_id::text = any(%s) "
                   "order by 1", run_ids) == [(r,) for r in run_ids]
    assert rows(w, "select state from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
                e["checkpoint_id"]) == [("evaluated",)]
    assert len(w.deployer.deployed) == 1
    # WR-B3-D8: the event and both decisions are D8's rows (one each, the replays add none)
    assert rows(w, "select count(*) from infrx.lab_checkpoint_events where checkpoint_id = %s",
                e["checkpoint_id"]) == [(1,)]
    assert rows(w, "select state, run_id::text from infrx.lab_checkpoint_decisions where "
                   "checkpoint_id = %s order by run_id", e["checkpoint_id"]) == [
        ("queued", r) for r in run_ids]


def test_b3_pg_a_crash_before_the_decision_is_one_run_and_a_malformed_one_none(world):
    w = on_pg(world, 2)
    run(checkpoints.subscribe(w.ledger, w.store, w.subscription(1), access=w.access,
                              user_id=DEV))
    e = event(w, 1)
    receive(w, e)
    w.ledger.crash_on_decide = 1
    with pytest.raises(Crash):
        handle(w, e)
    (decision,) = handle(w, e).values()
    assert rows(w, "select count(*) from infrx.lab_eval_runs where run_id = %s",
                decision["run_id"]) == [(1,)]
    bad = event(w, 2, data=b"junk")
    receive(w, bad)
    handle(w, bad)
    assert rows(w, "select state from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
                bad["checkpoint_id"]) == [("rejected",)]
    assert rows(w, "select count(*) from infrx.lab_eval_runs where run_id = %s",
                checkpoints.run_id_of(w.subscription(1)["subscription_id"],
                                      bad["checkpoint_id"])) == [(0,)]
    assert len(w.deployer.deployed) == 1      # the redelivery's deploy is the same one
    assert rows(w, "select count(*) from infrx.lab_checkpoint_rejections where checkpoint_id "
                   "= %s", bad["checkpoint_id"]) == [(1,)]     # WR-B3-D8: D8's first reason
