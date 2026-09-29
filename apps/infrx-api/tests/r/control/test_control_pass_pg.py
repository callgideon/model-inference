#!/usr/bin/env python3
"""WR-R2-3 on real PostgreSQL: the rollout role's pass loop (`python -m infrx.lab.workers
rollout`, composed from its env) over a released policy - D9's `PgReleaseStore` listing
(`releases_in`, 0048) and CAS, D7's policy record, L3's serving control over the seed's real
alias rows (`control_serving`, 0044's reads), the plan stored beside the release and - nothing
injected - R2's `Live` read by D9 (0054, WR-C6-LIVE / R244) from R1's assignments and the
admitted jobs they name (job rows written as fixtures, `tests/d/test_code_mutants_live.py`'s
`job`); the objects are in memory (the unit's are the Lab bucket).

Nothing assigned yet is held (never zeros); healthy traffic short of the evidence holds; a
breach in the jobs rolls the release back once, decided by `LAB_OPERATOR_ID`; the alias
already serves the baseline, so nothing is re-listed; the next pass steps the rolled-back
release again (R216: converge only) with no second decision; a plan whose budget is another
unit than the jobs settled in is refused (counted failed), never decided. Outside the mutant
runner: the fake-level oracles are `tests/w/test_lab_workers.py`'s rollout cases and their
`lw_rollout_*` mutants.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/r/control/test_control_pass_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.contracts.lab import records as lab
from infrx.lab.control.operations import serving_ref
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.rollouts import control as r2
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector
from infrx.state.lab_control import PgControlStore
from infrx.state.lab_rollout import PgReleaseStore

from ...d import checks_credit as cc
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d9_rollout as d9
from ...d.test_code_mutants_live import job, ref_of
from .test_control import plan
from .test_control_pg import DB, world  # noqa: F401

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("r2", "r1") else \
    "PostgreSQL only on the r2 (or r1) task-local key (INFRX_D_TASK=r2|r1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
OPERATOR = "00000090-0000-4000-8000-000000000090"   # a principal id (the decision record)


def run(coro):
    return asyncio.run(coro)


def test_r2_pg_the_rollout_pass_evaluates_a_running_release_on_d9s_live(world, monkeypatch):
    dsn = pgharness.dsn(DB)
    public = run(PgControlStore(connector(dsn)).deployment(cc.PUBLIC_DEPLOYMENT))
    baseline = serving_ref(public, run(PgCatalogDirectory(connector(dsn)).serving_revision(
        public.serving_version_id)))
    candidate = ref_of(world, cc.DEV_DEPLOYMENT)                     # ready_private: healthy
    payload = {**d9.policy(d9.uid(7, 0xb0), weights=(1_000,), endpoint=public.endpoint_id),
               "baseline_ref": baseline,
               "candidates": [{"serving_ref": candidate, "weight_bp": 1_000}]}
    ref = d7.publish(world, payload)
    policy_id = payload["policy_id"]
    releases = PgReleaseStore(connector(dsn))
    run(releases.start(ref, provider_org_id=d9.NEMO, plan_digest=r2.plan_digest(plan()),
                       decided_by=d9.USER, reason="canary 10%"))
    objects = InMemoryObjectStore()
    run(objects.put_if_absent(lab_workers.plan_key(d9.NEMO, policy_id),
                              plan().model_dump_json().encode(), "application/json"))
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])
    env = {"LAB_DATABASE_URL": dsn, "LAB_WORKER_HEALTH_PORT": "9", "LAB_S3_BUCKET": "unused",
           "LAB_OPERATOR_ID": OPERATOR}

    def one_pass():
        worker = lab_workers.compose("rollout", env, objects=objects)
        worker.tasks["rollout_pass"]().close()
        return run(steps["rollout pass"]())

    def decisions(of=ref):
        return world.execute(
            "select e.action, e.to_state, e.decided_by::text, e.reasons "
            "from infrx.lab_rollout_events e join infrx.lab_rollouts o using (policy_id) "
            "where o.policy_ref = %s and e.action <> 'start' order by e.fence", (of,)).fetchall()

    assert one_pass() == {"stepped": 0, "held": 1, "failed": 0}       # nothing assigned: held
    for n in range(1, 11):                                           # healthy, short of evidence
        job(world, ref, payload, d9.uid(n, 0x7c), candidate, "succeeded", ms=20)
    job(world, ref, payload, d9.uid(1, 0x7b), baseline, "succeeded", ms=20)
    assert one_pass() == {"stepped": 1, "held": 0, "failed": 0}
    assert run(releases.release(ref)).state == "running" and decisions() == [], "a hold decided"
    job(world, ref, payload, d9.uid(11, 0x7c), candidate, "failed", ms=20)   # 1/11 > 2%
    assert one_pass() == {"stepped": 1, "held": 0, "failed": 0}
    assert run(releases.release(ref)).state == "rolled_back"
    assert decisions() == [("rollback", "rolled_back", OPERATOR, ["error_rate"])]
    assert one_pass() == {"stepped": 1, "held": 0, "failed": 0}
    assert decisions() == [("rollback", "rolled_back", OPERATOR, ["error_rate"])], \
        "one decision, never two"
    serving = lab_workers.compose("rollout", env, objects=objects).wiring._serving
    assert run(serving.serving(public.endpoint_id))[0] == baseline, "the alias: the baseline"
    assert lab.ref_of(payload) == ref
    # a plan budgeted in PROVIDER_USD over jobs that settled in CREDIT: refused, never decided
    usd_plan = plan(budget={"unit": "PROVIDER_USD", "value": "100.00000000"})
    other = {**payload, "policy_id": d9.uid(8, 0xb0)}
    other_ref = d7.publish(world, other)
    run(releases.start(other_ref, provider_org_id=d9.NEMO, plan_digest=r2.plan_digest(usd_plan),
                       decided_by=d9.USER, reason="canary 10%"))
    run(objects.put_if_absent(lab_workers.plan_key(d9.NEMO, other["policy_id"]),
                              usd_plan.model_dump_json().encode(), "application/json"))
    job(world, other_ref, other, d9.uid(1, 0x8c), candidate, "succeeded", charged="1.00000000")
    assert one_pass() == {"stepped": 1, "held": 0, "failed": 1}     # the rolled-back one steps
    assert run(releases.release(other_ref)).state == "running" and decisions(other_ref) == []
