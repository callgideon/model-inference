#!/usr/bin/env python3
"""WR-LSQ-5: R2's controller over D9's real release rows - `PgReleaseStore` (0033 + 0039) as
its `ReleaseStore` on the task-local PostgreSQL (INFRX_D_TASK=r2, 57534; r1 when r2 is held
by another checkout). A NEMO canary policy revision is published through D7 and launched with
the plan's digest frozen; L3's serving alias stays `test_control.FakeServing` (L3's CAS is not
this seam). The concurrent-controller and restart drills of `test_control.py`, the ledger
swapped. Outside the mutant runner (N2/T2I's pattern): the oracles are the fake cases' mutants
(`r2_conflict_always_raised`, `r2_no_converge_on_restart`,
`r2_plan_digest_unchecked`, `r2_identity_is_the_full_ref`, ...).

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/r/control/test_control_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records as lab
from infrx.rollouts import control as r2
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_rollout import PgReleaseStore

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d9_rollout as d9
from .test_control import (BASE_RUN, CAND_RUN, CONTROLLER, HORIZON, OPERATOR, FakeServing, live,
                           plan, report)

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("r2", "r1") else \
    "PostgreSQL only on the r2 (or r1) task-local key (INFRX_D_TASK=r2|r1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_r2"


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d9.seed(conn)
        yield conn


class Release:
    """One launched revision: the policy object, its stored ref, the real store."""

    def __init__(self, conn, tag: int) -> None:
        payload = d9.policy(d9.uid(tag, 0xb0), weights=(1_000,), endpoint=d9.uid(tag, 0xe0))
        self.conn, self.policy = conn, lab.parse(payload)
        self.ref = d7.publish(conn, payload)
        assert self.ref == lab.ref_of(self.policy.model_dump(mode="json", by_alias=True))
        self.store = PgReleaseStore(connector(pgharness.dsn(DB)))
        asyncio.run(self.store.start(self.ref, provider_org_id=d9.NEMO,
                                     plan_digest=r2.plan_digest(plan()), decided_by=d9.USER,
                                     reason="canary 10%"))
        self.serving = FakeServing(current=self.policy.candidates[0].serving_ref)

    def controller(self) -> r2.Controller:
        return r2.Controller(self.store, self.serving, actor_id=CONTROLLER)

    def step(self, ctl, live_, rep=None):
        return ctl.step(self.policy, self.ref, plan(), live_, now=HORIZON, report=rep,
                        runs=(BASE_RUN, CAND_RUN))

    def decisions(self) -> list[tuple]:
        return self.conn.execute(
            "select e.action, e.to_state, e.decided_by::text, e.reasons from "
            "infrx.lab_rollout_events e join infrx.lab_rollouts o using (policy_id) where "
            "o.policy_ref = %s and e.action <> 'start' order by e.fence", (self.ref,)).fetchall()


def test_r2_pg_a_breach_rolls_back_exactly_once_under_concurrent_controllers(world) -> None:
    """ROLLOUT-RECOVER: two controllers read fence 1 and both see the breach; D9's CAS takes
    one decision, the loser rereads `rolled_back` and accepts it; the alias moves once."""
    rel = Release(world, 1)
    one, two = rel.controller(), rel.controller()

    async def both():
        return await asyncio.gather(rel.step(one, live(errors_=21)),
                                    rel.step(two, live(errors_=21)))
    assert [v.action for v in asyncio.run(both())] == ["rollback", "rollback"]
    assert rel.decisions() == [("rollback", "rolled_back", CONTROLLER, ["error_rate"])]
    got = asyncio.run(rel.store.release(rel.ref))
    assert (got.state, got.fence) == ("rolled_back", 2)
    assert rel.serving.current == rel.policy.baseline_ref and len(rel.serving.rollbacks) == 1


def test_r2_pg_a_restart_converges_without_a_second_decision_or_flapping(world) -> None:
    """Killed between D9's transition and L3's CAS: the restarted controller converges the
    alias from the stored `rolled_back` row, makes no second decision whatever the metrics
    and report say, and an approval of the rolled-back release is refused."""
    rel = Release(world, 2)
    rel.serving.fail = 1
    with pytest.raises(ConnectionError):
        asyncio.run(rel.step(rel.controller(), live(p99=9_001)))
    assert asyncio.run(rel.store.release(rel.ref)).state == "rolled_back"
    assert rel.serving.current != rel.policy.baseline_ref
    restarted = rel.controller()
    for _ in range(2):
        verdict = asyncio.run(rel.step(restarted, live(), rep=report()))
        assert (verdict.action, verdict.reasons) == ("rolled_back", ())
    assert rel.serving.current == rel.policy.baseline_ref and len(rel.serving.rollbacks) == 1
    assert rel.decisions() == [("rollback", "rolled_back", CONTROLLER, ["latency"])]
    with pytest.raises(errors.StateConflict):
        asyncio.run(restarted.approve(OPERATOR, rel.policy, rel.ref, plan(), live(),
                                      now=HORIZON, report=report(), runs=(BASE_RUN, CAND_RUN)))


def test_r2_pg_a_plan_other_than_the_launched_one_is_refused(world) -> None:
    """R2.a on the stored digest: a loosened plan never reaches a decision."""
    rel = Release(world, 3)
    with pytest.raises(errors.StateConflict, match="plan"):
        asyncio.run(rel.controller().step(rel.policy, rel.ref, plan(max_error_rate=0.5),
                                          live(errors_=21), now=HORIZON))
    assert rel.decisions() == []


def test_r2_pg_a_promoted_candidate_is_rolled_back_by_its_serving_identity(world) -> None:
    """E8L-F2 on D9's rows: the alias serves the candidate's serving revision under a fresh
    deployment revision (L3's promotion); the operator's stop records one decision and moves
    the alias back to the stored baseline."""
    rel = Release(world, 4)
    cand = rel.policy.candidates[0].serving_ref
    promoted = cand.replace(d9.uid(2, 0x5e), d9.uid(9, 0x5e))
    assert promoted != cand
    rel.serving.current = promoted
    asyncio.run(rel.controller().emergency_rollback(OPERATOR, rel.policy, rel.ref, now=HORIZON,
                                                    reason="pager"))
    assert rel.decisions() == [("rollback", "rolled_back", OPERATOR, ["operator:pager"])]
    assert rel.serving.current == rel.policy.baseline_ref and len(rel.serving.rollbacks) == 1


def test_r2_pg_a_later_listing_of_the_same_serving_version_is_left_alone(world) -> None:
    """0-RI-1 on D9's rows: after the operator's stop converged a promoted listing, a later
    re-promotion of the candidate's serving version (a fresh deployment revision) survives the
    pass over the stored `rolled_back` row; no second decision."""
    rel = Release(world, 5)
    cand = rel.policy.candidates[0].serving_ref
    rel.serving.current = cand.replace(d9.uid(2, 0x5e), d9.uid(9, 0x5e))
    ctl = rel.controller()
    asyncio.run(ctl.emergency_rollback(OPERATOR, rel.policy, rel.ref, now=HORIZON, reason="pager"))
    assert rel.serving.current == rel.policy.baseline_ref
    again = cand.replace(d9.uid(2, 0x5e), d9.uid(10, 0x5e))
    rel.serving.current, rel.serving.fence = again, rel.serving.fence + 1
    for _ in range(2):
        assert asyncio.run(rel.step(ctl, live())).action == "rolled_back"
    assert rel.serving.current == again and len(rel.serving.rollbacks) == 1
    assert rel.decisions() == [("rollback", "rolled_back", OPERATOR, ["operator:pager"])]
