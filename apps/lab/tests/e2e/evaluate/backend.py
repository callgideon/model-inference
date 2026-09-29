#!/usr/bin/env python3
"""LAB-E2E evaluate (E6L j10): the Lab's evaluation pages over lab-api's `/lab/v1/evaluations`
as the gateway composes it with LAB_EVALS on (`pilot._lab`: D7's REAL `PgLabDataStore`, B1's
freeze, the REAL L2), beside R186's control factory, on the task-local PostgreSQL (l4), D7's
seeded world.

The gateway's composition carries no experiments, catalog or B3 ledger port yet (WR-B4-2,
WR-LAB2-2, WR-B3-1: their SQL is 0043's, their adapters are not written):
`/_test/composition {"as": "gateway"}` serves exactly it; `{"as": "journey"}` fills those three
with the route suite's own fakes (`tests/g/lab_evaluations`, as `tests/b/backend.py`) over the
same real D7/B1/L2. `world.composed` says which ports the gateway's own composition carries, so
the gate reports NOT RUN until it carries them. Test-only doors: `/_test/state` (B1's worker
moving a run on, under D7's own state trigger) and `/_test/settle` (B2's stored report).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/evaluate/backend.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stack  # noqa: E402

PORTS = ("store", "experiments", "catalog", "ledger")


def main() -> None:
    from infrx.gateway.routes import lab_evaluations as le
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_l2sql_access as l2
    from tests.g import support
    from tests.g.lab_evaluations.test_lab_evaluations import (CANDIDATE, EVALUATOR, SERVING,
                                                               Experiments, Ledger)

    conn, dsn = stack.database("evaluate")
    d7.seed(conn)
    store = PgLabDataStore(connector(dsn))
    NEMO, DEV = l2.NEMO, l2.DEV

    async def publish():
        dataset = await store.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                      provider_org_id=NEMO, actor=DEV)
        harness_ref = await store.publish(harness(harness_id=uid(7, 0xa7)),
                                          provider_org_id=NEMO, actor=DEV)
        assert await store.put_evaluator(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID,
                                         actor=DEV) == EVALUATOR
        return dataset, harness_ref

    dataset, harness_ref = asyncio.run(publish())

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

    sock, url = stack.listen()
    app = stack.control_app(dsn, url)
    users = {"dev": DEV, "viewer": l2.VIEWER, "other_dev": l2.BOTH, "consumer": l2.C1}
    stack.door(app, dsn, users)
    gateway = stack.composed("lab_evals", dsn, url)["lab_evaluations"]
    composed = {name: getattr(gateway, name) is not None for name in PORTS}
    experiments = Experiments()
    journey = dataclasses.replace(gateway, experiments=experiments, ledger=Ledger(),
                                  catalog=Catalog())

    class Switch:
        """The mounted `LabEvaluations`: the gateway's own composition, or the journey's."""
        current = gateway

        def __getattr__(self, name):
            return getattr(self.current, name)

    switch = Switch()
    le.register(app, support.runtime(), switch)

    @app.post("/_test/composition")
    async def composition(body: dict):
        switch.current = {"gateway": gateway, "journey": journey}[body["as"]]
        return {"as": body["as"]}

    @app.post("/_test/state")
    async def state(body: dict):
        """B1's worker moving a run on: D7's own state trigger decides what is allowed."""
        conn.execute("update infrx.lab_eval_runs set state = %s where run_id = %s",
                     (body["state"], body["run_id"]))
        return {"state": body["state"]}

    @app.post("/_test/settle")
    async def settle(body: dict):
        """B2's worker storing its report on the experiment (WR-B-5)."""
        experiments.rows[(NEMO, body["experiment_id"])]["report"] = body["report"]
        return {"settled": True}

    world = {"A": NEMO, "B": l2.OTHER, "dataset": dataset, "servings": [SERVING, CANDIDATE],
             "users": users, "composed": composed,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           "experiments/catalog/B3 ledger: the route suite's fakes "
                           "(WR-B4-2, WR-LAB2-2, WR-B3-1 to compose)",
                           "B1's worker and B2's report (test doors)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
