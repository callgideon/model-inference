#!/usr/bin/env python3
"""WR-LIVE-DECIDE on real PostgreSQL: `python -m infrx.lab.workers rollout decide --approve`
of an expansion proposal (0043) over a launched release - D9's Live (0054) from R1's
assignments and the admitted jobs they name (job rows as fixtures, `tests/d/
test_code_mutants_live.py`'s `job`), D7's policy record, the plan stored beside it (objects in
memory: the unit's are the Lab bucket).

Nothing assigned is refused (held), the proposal stays pending; healthy traffic short of the
evidence is R2's hold, refused by name; nothing reaches D9. The D worlds freeze the database
clock, so R2's `expand` over real Live is unit-proven (`test_lab_workers.py`, `lw_expand_*`);
here that verdict is injected once to prove the decision half on real 0043: one CAS, the
release `approved` at fence 2, one `expand` event by the operator with the verdict's
evidence, the proposal approved; a second decision is refused. Outside the mutant runner.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/w/test_lab_workers_decide_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from infrx.lab.control.operations import serving_ref
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.rollouts import control as r2
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector
from infrx.state.lab_control import PgControlStore
from infrx.state.lab_rollout import PgReleaseProposals, PgReleaseStore

from ..d import checks_credit as cc
from ..d import pgharness
from ..d import test_d7_lab_data as d7
from ..d import test_d9_rollout as d9
from ..d.test_code_mutants_live import job, ref_of
from ..r.control.test_control import plan
from ..r.control.test_control_pg import DB, world  # noqa: F401

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("r2", "r1") else \
    "PostgreSQL only on the r2 (or r1) task-local key (INFRX_D_TASK=r2|r1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
OPERATOR = "00000090-0000-4000-8000-000000000090"


def run(coro):
    return asyncio.run(coro)


def test_lab_workers_pg_an_expansion_is_decided_only_on_r2s_expand_verdict(world, monkeypatch,
                                                                           capsys):
    dsn = pgharness.dsn(DB)
    public = run(PgControlStore(connector(dsn)).deployment(cc.PUBLIC_DEPLOYMENT))
    baseline = serving_ref(public, run(PgCatalogDirectory(connector(dsn)).serving_revision(
        public.serving_version_id)))
    candidate = ref_of(world, cc.DEV_DEPLOYMENT)                     # ready_private: healthy
    payload = {**d9.policy(d9.uid(9, 0xb0), weights=(1_000,), endpoint=public.endpoint_id),
               "baseline_ref": baseline,
               "candidates": [{"serving_ref": candidate, "weight_bp": 1_000}]}
    ref = d7.publish(world, payload)
    releases, proposals = PgReleaseStore(connector(dsn)), PgReleaseProposals(connector(dsn))
    run(releases.start(ref, provider_org_id=d9.NEMO, plan_digest=r2.plan_digest(plan()),
                       decided_by=d9.USER, reason="canary 10%"))
    objects = InMemoryObjectStore()
    run(objects.put_if_absent(lab_workers.plan_key(d9.NEMO, payload["policy_id"]),
                              plan().model_dump_json().encode(), "application/json"))
    monkeypatch.setattr(lab_workers, "lab_objects", lambda mode, env: objects)
    expand = str(uuid.uuid4())
    run(proposals.propose(ref, provider_org_id=d9.NEMO, proposal_id=expand, kind="expand",
                          fence=1, proposed_by=d9.USER))
    env = {"LAB_DATABASE_URL": dsn, "LAB_OPERATOR_ID": OPERATOR, "LAB_S3_BUCKET": "unused"}

    def approve():
        capsys.readouterr()
        code = lab_workers.main(["rollout", "decide", "--policy-ref", ref, "--proposal-id",
                                 expand, "--approve", "--reason", "widen"], env=dict(env))
        return code, capsys.readouterr().err

    def state():
        mine = [p["state"] for p in run(proposals.proposals(provider_org_id=d9.NEMO))
                if p["proposal_id"] == expand]
        events = world.execute(
            "select e.action, e.to_state, e.decided_by::text, e.fence, e.decision, "
            "e.evidence_refs from infrx.lab_rollout_events e join infrx.lab_rollouts o "
            "using (policy_id) where o.policy_ref = %s and e.action <> 'start' order by e.fence",
            (ref,)).fetchall()
        release = run(releases.release(ref))
        return mine, events, (release.state, release.fence)

    code, err = approve()                                            # nothing assigned: held
    assert code == 1 and "no admitted request is assigned" in err, err
    assert state() == (["proposed"], [], ("running", 1))
    for n in range(1, 11):                                           # healthy, short of evidence
        job(world, ref, payload, d9.uid(n, 0x9c), candidate, "succeeded", ms=20)
    job(world, ref, payload, d9.uid(1, 0x9b), baseline, "succeeded", ms=20)
    assert run(releases.live(ref)) is not None
    code, err = approve()                                            # R2 holds: by name
    assert code == 1 and "R2's verdict is hold" in err and "min_requests" in err, err
    assert state() == (["proposed"], [], ("running", 1))
    # the verdict R2 would give at the horizon over a fresh Live (unit-proven), injected once
    evidence = d9.evidence(world, 0xe9)
    monkeypatch.setattr(r2, "evaluate", lambda *a, **kw: r2.Verdict("expand", (), (evidence,)))
    assert approve() == (0, "")
    assert state() == (["approved"], [("expand", "approved", OPERATOR, 2, "expand",
                                         [evidence])],
                       ("approved", 2)), state()
    code, err = approve()                                            # decided once
    assert code == 1 and "was not decided" in err, err
    assert len(state()[1]) == 1
