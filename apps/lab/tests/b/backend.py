#!/usr/bin/env python3
"""B4 swap's real backend for `stack.test.ts`: lab-api's `/lab/v1/evaluations`
(`lab_evaluations.register`, WR-B4-1) served as merged over the REAL D7 (`PgLabDataStore`, 0029/0034:
the runs, the dataset, harness, evaluator and external run records) and the REAL L2
(`LabAccess(PgAccessStore)`: every actor, role and B1's scheduling gate on the database) on the
task-local PostgreSQL (`INFRX_D_TASK=b3`, port 57522), D7's seeded world. The experiments
(WR-B4-2), catalog (WR-LAB2-2) and B3 ledger listing (WR-B3-1) ports are the route suite's own fakes:
their tables (0042/0043) are not on this base. Test-only: the bearer-token-per-user stand-in for
`GoTrueSessions`, and three `/_test/*` doors standing in for the workers (a run's state moving on,
B2's stored report, B3's decision).

    INFRX_D_TASK=b3 uv run --frozen --project apps/infrx-api python apps/lab/tests/b/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "infrx-api"
sys.path.insert(0, str(API))

from fastapi import FastAPI, Request  # noqa: E402


def main() -> None:
    import uvicorn

    from infrx.gateway.routes import lab_evaluations as le
    from infrx.lab.access import LabAccess
    from infrx.state import migrations
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_data import PgLabDataStore
    from tests.b.checkpoints.world import World as B3World
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import pgharness
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_l2sql_access as l2
    from tests.g import support
    from tests.g.lab_evaluations.test_lab_evaluations import (CANDIDATE, EVALUATOR, SERVING,
                                                               Experiments, Ledger, Sessions,
                                                               token)

    if os.environ.get("INFRX_D_TASK") != "b3":
        raise SystemExit("the b3 task-local key only (INFRX_D_TASK=b3)")
    db = f"{pgharness.DATABASE}_labui"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    conn = pgharness.connect(db)
    d7.seed(conn)
    connect = connector(pgharness.dsn(db))
    store = PgLabDataStore(connect)
    NEMO, OTHER, DEV = l2.NEMO, l2.OTHER, l2.DEV

    async def publish():
        dataset = await store.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                      provider_org_id=NEMO, actor=DEV)
        harness_ref = await store.publish(harness(harness_id=uid(7, 0xa7)),
                                          provider_org_id=NEMO, actor=DEV)
        assert await store.put_evaluator(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID,
                                         actor=DEV) == EVALUATOR
        b3 = B3World.__new__(B3World)
        b3.dataset = dataset
        external = await store.publish(b3.external_run(uid(7, 0xe3)), provider_org_id=NEMO,
                                       actor=DEV)
        return dataset, harness_ref, external

    dataset, harness_ref, external = asyncio.run(publish())

    class Catalog:
        """WR-LAB2-2's listing (the route suite's fake): NEMO's own records only."""

        async def catalog(self, provider_org_id):
            if provider_org_id != NEMO:
                return {"datasets": [], "harnesses": [], "servings": [], "evaluators": []}
            return {"datasets": [{"ref": dataset, "label": "support-v1"}],
                    "harnesses": [{"ref": harness_ref, "harness_id": uid(7, 0xa7), "version": 1,
                                   "adapter": "text"}],
                    "servings": [{"ref": SERVING, "label": "base"},
                                 {"ref": CANDIDATE, "label": "tuned"}],
                    "evaluators": [{"ref": EVALUATOR, "label": "exact match"}]}

        async def evaluator(self, provider_org_id, evaluator_ref):
            if (provider_org_id, evaluator_ref) != (NEMO, EVALUATOR):
                raise le.errors.NotFound("no such evaluator for this provider")
            return SPEC

    users = {"dev": DEV, "viewer": l2.VIEWER, "other_dev": l2.BOTH, "consumer": l2.C1}
    experiments, ledger = Experiments(), Ledger()
    x = le.LabEvaluations(Sessions(users.values()), LabAccess(PgAccessStore(connect)),
                          store=store, experiments=experiments, ledger=ledger, catalog=Catalog())
    app = FastAPI()
    le.register(app, support.runtime(), x)

    @app.post("/_test/state")
    async def state(request: Request):
        """B1's worker moving a run on: D7's own state trigger decides what is allowed."""
        body = await request.json()
        conn.execute("update infrx.lab_eval_runs set state = %s where run_id = %s",
                     (body["state"], body["run_id"]))
        return {"state": body["state"]}

    @app.post("/_test/settle")
    async def settle(request: Request):
        """B2's worker storing its report on the experiment (WR-B-5)."""
        body = await request.json()
        experiments.rows[(NEMO, body["experiment_id"])]["report"] = body["report"]
        return {"settled": True}

    @app.post("/_test/decide")
    async def decide(request: Request):
        """B3's ledger recording one decision for a received checkpoint."""
        body = await request.json()
        ledger.decided[(body["subscription_id"], body["checkpoint_id"])] = body["decision"]
        return {"decided": True}

    world = {"A": NEMO, "B": OTHER, "dataset": dataset, "harness": harness_ref,
             "harness_id": uid(7, 0xa7), "external": external, "evaluator": EVALUATOR,
             "servings": [SERVING, CANDIDATE],
             "tokens": {name: token(user) for name, user in users.items()}}
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))

    async def serve():
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY {port} {json.dumps(world)}", flush=True)
        await task
    try:
        asyncio.run(serve())
    finally:
        conn.close()


if __name__ == "__main__":
    os.chdir(API)
    main()
