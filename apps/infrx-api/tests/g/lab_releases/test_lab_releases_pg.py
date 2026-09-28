#!/usr/bin/env python3
"""WR-R4-1's real half: `/lab/v1/releases` proposals over the merged real pieces - L2's
`PgAccessStore` (the actor, the role and the proposal's clock are the database's) on the
task-local PostgreSQL of key r2, and R2's own `Controller` and `evaluate` deciding the verdict
the read model serves and the operator's approval and emergency rollback.

D9 is NOT real here: `PgReleaseStore` (lab-sql 0033/0039, codex/w5-lab-sql-lw2) is not merged,
so D9 is R2's own port fake (`tests/r/control`: one row, CAS on a fence, declared moves only),
and its policy fixture is R2's (provider P, served under NEMO's listing by the read-model fake).
The read models (WR-R4-1's lab-sql half) and the proposal store (WR-R4-2) stay this suite's
fakes. Outside the mutant runner (P1's PG pattern); the oracles are the fake-world cases'
mutants.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/g/lab_releases/test_lab_releases_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.gateway.routes import lab_releases as lr
from infrx.lab.access import LabAccess
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore

from .. import support
from ...d import pgharness
from ...d import test_l2sql_access as l2
from ...r.control import test_control as r2w
from .test_lab_releases import Proposals, Records, Sessions, token

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "r2" else \
    "PostgreSQL only on the r2 task-local key (INFRX_D_TASK=r2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lab2r"
NEMO, DEV, ADMIN, VIEWER, C1 = l2.NEMO, l2.DEV, l2.ADMIN, l2.VIEWER, l2.C1
PROPOSE = lr.RELEASES_PATH + "/proposals"


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        l2.seed(conn)
    d9, records = r2w.FakeReleases(), Records()
    access = PgAccessStore(connector(pgharness.dsn(DB)))
    x = lr.LabReleases(Sessions((DEV, ADMIN, VIEWER, C1)), LabAccess(access),
                       records=records, proposals=Proposals(), store=d9)
    app = FastAPI()
    lr.register(app, support.runtime(), x)
    yield d9, records, r2w.controller(d9, r2w.FakeServing()), \
        TestClient(app, raise_server_exceptions=False), access


def served(d9, records, verdict) -> None:
    """The read model's row: D9's state and fence, R2's latest verdict (WR-R4-1 lab-sql)."""
    records.of(NEMO)["releases"] = [{
        "policy_ref": r2w.POLICY_REF, "state": d9.row.state, "fence": d9.row.fence,
        "verdict": {"action": verdict.action, "reasons": list(verdict.reasons),
                    "evidence_refs": list(verdict.evidence_refs),
                    "evaluated_at": "2026-09-28T10:00:00Z"}}]


def propose(c, user, kind, fence):
    return c.post(PROPOSE, params={"provider_org_id": NEMO},
                  json={"kind": kind, "policy_ref": r2w.POLICY_REF, "fence": fence},
                  headers={"authorization": f"Bearer {token(user)}"})


def test_lab_releases_pg__proposals_follow_r2_and_d9s_fence_on_l2s_roles(world):
    """ROLLOUT-PIN on L2 and R2: an inconclusive B2 report holds (R2), so no expansion is
    proposed; on an accepting report the administrator's expansion is stored `proposed` at the
    database's clock with D9's fence, while a viewer, a developer and a consumer-only user are
    refused on L2's roles; once the operator approves through R2 (D9's CAS moves the fence)
    the page's fence is stale, and after R2's emergency rollback nothing is proposed."""
    d9, records, ctl, c, access = world
    held = asyncio.run(r2w.step(ctl, rep=r2w.report("inconclusive")))
    assert (held.action, held.reasons) == ("hold", ("report_inconclusive",))
    served(d9, records, held)
    answer = propose(c, ADMIN, "expand", d9.row.fence)
    assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"})

    verdict = asyncio.run(r2w.step(ctl, rep=r2w.report()))
    assert (verdict.action, d9.row.state, d9.decisions) == ("expand", "running", [])
    served(d9, records, verdict)
    for user, status in ((VIEWER, 403), (DEV, 403), (C1, 403)):
        answer = propose(c, user, "expand", d9.row.fence)
        assert (answer.status_code, answer.json()) == (status, {"refusal": "denied"}), user
    made = propose(c, ADMIN, "expand", d9.row.fence)
    assert made.status_code == 201, made.json()
    proposal = made.json()
    assert (proposal["kind"], proposal["fence"], proposal["state"]) == ("expand", 1, "proposed")
    assert proposal["proposed_at"] == \
        asyncio.run(access.db_now()).strftime("%Y-%m-%dT%H:%M:%SZ")   # PostgreSQL's clock
    assert (d9.row.state, d9.row.fence) == ("running", 1)    # a proposal moves nothing

    asyncio.run(ctl.approve(r2w.OPERATOR, r2w.POLICY, r2w.POLICY_REF, r2w.plan(), r2w.live(),
                            now=r2w.HORIZON, report=r2w.report(),
                            runs=(r2w.BASE_RUN, r2w.CAND_RUN)))
    assert (d9.row.state, d9.row.fence) == ("approved", 2)
    stale = propose(c, ADMIN, "rollback", 1)             # the page before the approval
    assert (stale.status_code, stale.json()) == (409, {"refusal": "conflict"})

    asyncio.run(ctl.emergency_rollback(r2w.OPERATOR, r2w.POLICY, r2w.POLICY_REF,
                                       now=r2w.HORIZON, reason="pager"))
    served(d9, records, verdict)
    assert (d9.row.state, d9.row.fence) == ("rolled_back", 3)
    for kind in ("expand", "rollback"):
        answer = propose(c, ADMIN, kind, 3)
        assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"}), kind
    assert c.get(lr.RELEASES_PATH, params={"provider_org_id": NEMO}, headers={
        "authorization": f"Bearer {token(VIEWER)}"}).json()["data"]["proposals"] == [proposal]
