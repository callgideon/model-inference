#!/usr/bin/env python3
"""AP-10 10a on real SQL: `/lab/v1/evaluations` with `infrx.lab.evaluation.evaluation_ports`
(0043's experiments and checkpoint listing, 0034's evaluator, D7's records) on ap10's
task-local PostgreSQL, every migration, D7's seeded world. The session verifier and the L2
port are B1's fakes (as `tests/g/lab_evaluations/test_lab_evaluations_pg.py`).

Oracles: an authorized member with nothing reads 200 `[]` (the base composition: 503) and
its catalog is 0066's listing of its own records and ready private dev serving (before
WR-UXVF-1: 503), another provider's none of them; a
stored experiment and a subscription are read back through the routes from their SQL rows;
a resubmit is the first launch; a database that does not answer is 503, never `[]`.
Outside the mutant runner (B1's `_pg` pattern); `test_evaluation_ports.py` carries the
decisions' mutants.

    INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_evaluation_ports_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.evaluation import runner
from infrx.gateway.routes import lab_evaluations as le
from infrx.lab import evaluation as ev
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from ..b.checkpoints.world import World as B3World
from ..b.runner.world import (DEV, EVALUATOR_ID, NEMO, OTHER, OUTSIDER, SPEC, access, harness,
                              manifest, uid)
from ..d import checks_credit as cc
from ..d import pgharness
from ..d import test_d7_lab_data as d7
from ..g import support
from ..g.lab_evaluations.test_lab_evaluations import (CANDIDATE, EVALUATOR, PROTOCOL, SERVING,
                                                      Sessions, token)

TASK = os.environ.get("INFRX_D_TASK")
_reason = pgharness.unavailable() if TASK == "ap10" else \
    "PostgreSQL only on the ap10 task-local key (INFRX_D_TASK=ap10)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_ap10e"


def run(coro):
    return asyncio.run(coro)


def lab_access():
    lab = access()
    lab.store.provider_names = {NEMO: "Nemo", OTHER: "Other"}
    return lab


def client(connect, store) -> TestClient:
    x = le.LabEvaluations(Sessions((DEV, OUTSIDER)), lab_access(), store=store,
                          **ev.evaluation_ports(connect))
    app = FastAPI()
    le.register(app, support.runtime(), x)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        connect = connector(pgharness.dsn(DB))
        store = PgLabDataStore(connect)
        dataset = run(store.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                    provider_org_id=NEMO, actor=DEV))
        harness_ref = run(store.publish(harness(harness_id=uid(7, 0xa7)),
                                        provider_org_id=NEMO, actor=DEV))
        assert run(store.put_evaluator(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID,
                                       actor=DEV)) == EVALUATOR
        yield conn, connect, store, dataset, harness_ref


def get(c, path, user=DEV, provider=NEMO):
    return c.get(f"{le.EVALS_PREFIX}/{path}", params={"provider_org_id": provider},
                 headers={"authorization": f"Bearer {token(user)}"})


def test_ap10_pg__authorized_empty_reads_are_200_empty_and_the_catalog_is_0066s(world):
    conn, connect, store, dataset, harness_ref = world
    c = client(connect, store)
    for path in ("experiments", "runs", "subscriptions"):
        answer = get(c, path, OUTSIDER, OTHER)
        assert (answer.status_code, answer.json()) == (200, {"data": []}), path
    ready = conn.execute("select infrx.lab_serving_ref(%s)", (cc.DEV_DEPLOYMENT,)).fetchone()[0]
    answer = get(c, "catalog")
    assert answer.status_code == 200, answer.text
    mine = {kind: [o["ref"] for o in listed] for kind, listed in answer.json()["data"].items()}
    assert (dataset in mine["datasets"], harness_ref in mine["harnesses"],
            EVALUATOR in mine["evaluators"], mine["servings"]) == (True, True, True, [ready]), mine
    answer = get(c, "catalog", OUTSIDER, OTHER)
    theirs = {kind: [o["ref"] for o in listed] for kind, listed in answer.json()["data"].items()}
    assert answer.status_code == 200 and theirs["servings"] == [] and \
        not {dataset, harness_ref, EVALUATOR} & {r for refs in theirs.values() for r in refs}, theirs


def test_ap10_pg__a_stored_experiment_is_read_back_and_a_resubmit_is_the_first_launch(world):
    """`put` then `freeze` as the launch route orders them: 0043's row names the two run
    records `freeze` created; the routes list the experiment and its two D7 runs."""
    conn, connect, store, dataset, harness_ref = world
    experiment = uid(2, 0xe0)
    launch = {"experiment_id": experiment, "dataset_ref": dataset, "harness_ref": harness_ref,
              "evaluator_ref": EVALUATOR, "baseline_serving_ref": SERVING,
              "candidate_serving_ref": CANDIDATE, "seed": 7, "max_cases": 3,
              "run_limit": {"unit": "CREDIT", "value": "10.00000000"}, "protocol": PROTOCOL}
    x = ev.evaluation_ports(connect)["experiments"]

    def put(created_at, wanted=launch):
        return run(x.put(NEMO, {"experiment_id": experiment, "created_at": created_at,
                                "launch": wanted, "report": None}, actor=DEV))
    row = put("2026-10-01T10:00:00Z")
    wanted = le.Launch.model_validate(launch)
    for arm in le.ARMS:
        run(runner.freeze(store, le._run_payload(NEMO, wanted, arm, row["created_at"]),
                          evaluator=SPEC, access=lab_access(), user_id=DEV,
                          provider_org_id=NEMO))
    assert put("2026-10-01T11:00:00Z") == {**row, "launch": launch}
    with pytest.raises(errors.IdempotencyConflict):
        put("2026-10-01T11:00:00Z", {**launch, "seed": 8})
    assert conn.execute("select created_by, protocol_digest from infrx.lab_experiments "
                        "where experiment_id = %s", (experiment,)).fetchone() == \
        (DEV, ev._digest(PROTOCOL))
    c = client(connect, store)
    (listed,) = get(c, "experiments").json()["data"]
    ids = [le.run_id(experiment, arm) for arm in le.ARMS]
    assert (listed["experiment_id"], listed["created_at"], listed["report"]) == \
        (experiment, "2026-10-01T10:00:00Z", None)
    assert [listed[arm]["run_id"] for arm in le.ARMS] == ids
    assert sorted(r["run_id"] for r in get(c, "runs").json()["data"]) == sorted(ids)
    assert get(c, "experiments", OUTSIDER, OTHER).json() == {"data": []}


def test_ap10_pg__a_subscription_is_stored_and_listed_through_the_routes(world):
    """POST /subscriptions over 0034's evaluator and D8's ledger, read back from 0043's
    listing without the evaluator spec or its owner."""
    _, connect, store, dataset, harness_ref = world
    b3 = B3World.__new__(B3World)
    b3.dataset = dataset
    external = run(store.publish(b3.external_run(uid(3, 0xe3)), provider_org_id=NEMO,
                                 actor=DEV))
    body = {"subscription_id": uid(3, 0x5b), "external_run_ref": external,
            "dataset_ref": dataset, "harness_ref": harness_ref, "evaluator_ref": EVALUATOR,
            "seed": 7, "max_cases": 3, "run_limit": {"unit": "CREDIT", "value": "10.00000000"},
            "limit": {"unit": "CREDIT", "value": "100.00000000"}, "max_active": 5,
            "policy": "every"}
    c = client(connect, store)
    made = c.post(f"{le.EVALS_PREFIX}/subscriptions", params={"provider_org_id": NEMO},
                  json=body, headers={"authorization": f"Bearer {token(DEV)}"})
    assert made.status_code == 201, made.json()
    (listed,) = get(c, "subscriptions").json()["data"]
    assert listed == made.json() and listed["decisions"] == []
    assert listed["subscription_id"] == body["subscription_id"]
    assert "evaluator" not in listed and "owner_user_id" not in listed


def test_ap10_pg__a_database_that_does_not_answer_is_503_never_empty(world):
    dead = connector("postgresql://infrx:x@127.0.0.1:9/none?connect_timeout=2")
    c = client(dead, PgLabDataStore(dead))
    for path in ("experiments", "runs", "subscriptions", "catalog"):
        answer = get(c, path)
        assert (answer.status_code, answer.json()) == (503, {"refusal": "unavailable"}), path
