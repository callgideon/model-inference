#!/usr/bin/env python3
"""WR-B4-1's real half: `/lab/v1/evaluations` over the real D7 store (`PgLabDataStore`, 0029, D7's
seeded world: C1's grant to NEMO and one source, plus B1's evaluator registered through 0034's
`put_evaluator`) on the task-local PostgreSQL of key b3. The
experiments, catalog and ledger ports stay this suite's fakes (no table yet: WR-B4-2, WR-B3-1,
WR-LAB2-2); the membership is B1's fake L2 port.

EVAL-DURABLE: a launch is two real D7 runs, and its resubmit the same two. Cancel: 0029's
`lab_cancel_run` updates whatever the state; its state trigger refuses succeeded/failed ->
cancelled, but a cancelled run cancels again (a 200 without the route's guard) - with it, all
three are 409 and the state is untouched. Outside the mutant runner (B1's pattern); the oracles
are the fake-world cases' mutants.

    INFRX_D_TASK=b3 uv run --frozen pytest -q tests/g/lab_evaluations/test_lab_evaluations_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.gateway.routes import lab_evaluations as le
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from .. import support
from ...b.runner.world import (DEV, EVALUATOR_ID, NEMO, OTHER, SPEC, access, harness, manifest,
                               uid)
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from .test_lab_evaluations import (CANDIDATE, EVALUATOR, EXPERIMENT, PROTOCOL, SERVING,
                                   Experiments, Ledger, Sessions, token)

TASK = os.environ.get("INFRX_D_TASK")
_reason = pgharness.unavailable() if TASK == "b3" else \
    "PostgreSQL only on the b3 task-local key (INFRX_D_TASK=b3)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lab2e"


class Catalog:
    def __init__(self, dataset: str, harness_ref: str) -> None:
        self.dataset, self.harness = dataset, harness_ref

    async def catalog(self, provider_org_id):
        return {"datasets": [{"ref": self.dataset, "label": "d"}],
                "harnesses": [{"ref": self.harness}], "evaluators": [{"ref": EVALUATOR}],
                "servings": [{"ref": SERVING}, {"ref": CANDIDATE}]}

    async def evaluator(self, provider_org_id, evaluator_ref):
        assert (provider_org_id, evaluator_ref) == (NEMO, EVALUATOR)
        return SPEC


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        store = PgLabDataStore(connector(pgharness.dsn(DB)))
        dataset = asyncio.run(store.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                            provider_org_id=NEMO, actor="dev@nemo"))
        harness_ref = asyncio.run(store.publish(harness(harness_id=uid(7, 0xa7)),
                                                provider_org_id=NEMO, actor="dev@nemo"))
        registered = asyncio.run(store.put_evaluator(SPEC, provider_org_id=NEMO,
                                                     evaluator_id=EVALUATOR_ID,
                                                     actor="dev@nemo"))
        assert registered == EVALUATOR     # 0034: publish refuses an unregistered evaluator
        lab = access()
        lab.store.provider_names = {NEMO: "Nemo", OTHER: "Other"}
        x = le.LabEvaluations(Sessions((DEV,)), lab, store=store, experiments=Experiments(),
                              ledger=Ledger(), catalog=Catalog(dataset, harness_ref))
        app = FastAPI()
        le.register(app, support.runtime(), x)
        yield conn, TestClient(app, raise_server_exceptions=False), dataset, harness_ref, store


def call(c, method, path, body=None):
    return c.request(method, f"{le.EVALS_PREFIX}/{path}", params={"provider_org_id": NEMO},
                     json=body, headers={"authorization": f"Bearer {token(DEV)}"})


def state(conn, run_id: str) -> str:
    return conn.execute("select state from infrx.lab_eval_runs where run_id = %s",
                        (run_id,)).fetchone()[0]


def test_lab_evaluations_pg__a_launch_is_two_real_d7_runs_once_and_a_finished_run_stays(world):
    conn, c, dataset, harness_ref, store = world
    launch = {"experiment_id": EXPERIMENT, "dataset_ref": dataset, "harness_ref": harness_ref,
              "evaluator_ref": EVALUATOR, "baseline_serving_ref": SERVING,
              "candidate_serving_ref": CANDIDATE, "seed": 7, "max_cases": 3,
              "run_limit": {"unit": "CREDIT", "value": "10.00000000"}, "protocol": PROTOCOL}
    first = call(c, "POST", "experiments", launch)
    assert first.status_code == 202, first.json()
    ids = [le.run_id(EXPERIMENT, arm) for arm in le.ARMS]
    assert [first.json()[arm]["run_id"] for arm in le.ARMS] == ids
    again = call(c, "POST", "experiments", launch)
    assert (again.status_code, again.json()) == (202, first.json())
    rows = conn.execute("select run_id::text, state, (select count(*) from infrx.lab_eval_cases "
                        "k where k.run_id = r.run_id) from infrx.lab_eval_runs r "
                        "where run_id = any(%s::uuid[]) order by run_id", (ids,)).fetchall()
    assert rows == sorted((rid, "queued", 3) for rid in ids)
    base, cand = ids
    for step in ("running", "succeeded"):
        conn.execute("update infrx.lab_eval_runs set state = %s where run_id = %s", (step, base))
    answer = call(c, "POST", f"runs/{base}/cancel")
    assert (answer.status_code, answer.json(), state(conn, base)) \
        == (409, {"refusal": "conflict"}, "succeeded")
    assert call(c, "POST", f"runs/{cand}/cancel").status_code == 200
    assert state(conn, cand) == "cancelled"
    answer = call(c, "POST", f"runs/{cand}/cancel")
    assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"})
    # D7 alone: a cancelled run cancels again (no-op), a succeeded one is refused by the trigger
    assert asyncio.run(store.cancel_run(cand, provider_org_id=NEMO))["state"] == "cancelled"
    with pytest.raises(errors.StateConflict):
        asyncio.run(store.cancel_run(base, provider_org_id=NEMO))
    listed = call(c, "GET", "runs").json()["data"]
    assert [(r["run_id"], r["state"]) for r in listed] == [(base, "succeeded"),
                                                            (cand, "cancelled")]
