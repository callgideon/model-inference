#!/usr/bin/env python3
"""WR-C5-REPORT + WR-C5-PLAN on real PostgreSQL: a release launched by `python -m
infrx.lab.workers rollout launch` (the plan stored beside it, D9 started with its digest) is
stepped by the rollout role's pass on its B2 report - B4's experiment (0043) of the release's
baseline and candidate runs under the plan's protocol, with B2's report stored in D7 (0034) -
over D9, D7 and L3's serving control on the seed's real alias rows. R1's aggregates are the one
input injected (WR-C5-LIVE), healthy on every operational bound; the objects are in memory.

A rejecting report rolls the release back once, the decision carrying the report's two runs
as evidence and `quality_reject`; an experiment of the same runs under another protocol is
never used. Outside the mutant runner: the oracles are `tests/w/test_lab_workers.py`'s B2
case and its `lw_report_*` mutants.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/r/control/test_control_report_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from infrx.contracts.lab import records as lab
from infrx.lab.control.operations import serving_ref
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector
from infrx.state.lab_control import PgControlStore
from infrx.state.lab_data import PgLabDataStore, PgLabReads

from ...b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
from ...d import checks_credit as cc
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d9_rollout as d9
from .test_control import BASE_RUN, CAND_RUN, PROTOCOL, digest, live, plan, report
from .test_control_pg import DB, world  # noqa: F401

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("r2", "r1") else \
    "PostgreSQL only on the r2 (or r1) task-local key (INFRX_D_TASK=r2|r1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
OPERATOR = "00000092-0000-4000-8000-000000000092"


def run(coro):
    return asyncio.run(coro)


def test_r2_pg_a_launched_release_is_rolled_back_on_its_rejecting_b2_report(
        world, monkeypatch, tmp_path):
    dsn = pgharness.dsn(DB)
    connect = connector(dsn)
    data = PgLabDataStore(connect)
    public = run(PgControlStore(connect).deployment(cc.PUBLIC_DEPLOYMENT))
    baseline = serving_ref(public, run(PgCatalogDirectory(connect).serving_revision(
        public.serving_version_id)))
    payload = {**d9.policy(d9.uid(41, 0xb0), weights=(1_000,), endpoint=public.endpoint_id),
               "baseline_ref": baseline}
    policy_ref = d7.publish(world, payload)
    candidate = payload["candidates"][0]["serving_ref"]

    async def suite() -> dict:
        dataset = await data.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=41),
                                     provider_org_id=d9.NEMO, actor=d9.USER)
        harness_ref = await data.publish(harness(harness_id=uid(41, 0xa7)),
                                         provider_org_id=d9.NEMO, actor=d9.USER)
        evaluator = await data.put_evaluator(SPEC, provider_org_id=d9.NEMO,
                                             evaluator_id=EVALUATOR_ID, actor=d9.USER)
        return {"provider_org_id": d9.NEMO, "dataset_ref": dataset, "harness_ref": harness_ref,
                "evaluator_ref": evaluator}
    common = run(suite())
    base_run = {**BASE_RUN, **common, "serving_ref": baseline}
    cand_run = {**CAND_RUN, **common, "serving_ref": candidate}
    refs = [d7.publish(world, r) for r in (base_run, cand_run)]
    for ref in refs:
        d7.ok(world, "lab_create_run", {"provider_org_id": d9.NEMO, "run_ref": ref})
    reads = PgLabReads(connect)
    for protocol, outcome in (({**PROTOCOL, "margin": 0.5}, "accept"), (PROTOCOL, "reject")):
        run(reads.put_experiment(str(uuid.uuid4()), provider_org_id=d9.NEMO, protocol=protocol,
                                 protocol_digest=digest(protocol), baseline_run_ref=refs[0],
                                 candidate_run_ref=refs[1], actor=d9.USER))
        run(data.put_eval_report(report(outcome, base_run=base_run, cand_run=cand_run,
                                        protocol=protocol, reasons=("slice:van",)),
                                 provider_org_id=d9.NEMO, actor=d9.USER))

    objects = InMemoryObjectStore()
    monkeypatch.setattr(lab_workers, "lab_objects", lambda mode, env: objects)
    stored = tmp_path / "plan.json"
    stored.write_text(plan().model_dump_json())
    env = {"LAB_DATABASE_URL": dsn, "LAB_WORKER_HEALTH_PORT": "9", "LAB_S3_BUCKET": "lab",
           "LAB_OPERATOR_ID": OPERATOR}
    assert lab_workers.main(["rollout", "launch", "--policy-ref", policy_ref, "--plan",
                             str(stored), "--reason", "canary 10%"], env=dict(env)) == 0
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])

    async def aggregates(item):
        return live()                         # every operational bound met exactly
    worker = lab_workers.compose("rollout", env, objects=objects, live=aggregates)
    worker.tasks["rollout_pass"]().close()
    assert run(steps["rollout pass"]()) == {"stepped": 1, "held": 0, "failed": 0}
    decisions = world.execute(
        "select e.action, e.decided_by::text, e.evidence_refs, e.reasons from "
        "infrx.lab_rollout_events e join infrx.lab_rollouts o using (policy_id) where "
        "o.policy_ref = %s and e.action <> 'start' order by e.fence", (policy_ref,)).fetchall()
    assert decisions == [("rollback", OPERATOR, refs, ["quality_reject", "slice:van"])], \
        decisions
    assert lab.ref_of(base_run) == refs[0]
