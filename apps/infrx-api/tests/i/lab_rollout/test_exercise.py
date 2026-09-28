#!/usr/bin/env python3
"""I7.b (ROLLOUT-RECOVER): the rollback exercise of infra/lab/workers/rollout/RUNBOOK.md §5,
run locally over R2's real `Controller` and R2's D9/L3 fakes (lab-sql's and L3's stores are
not on the base; rerun on them at their merge). Every pass is a fresh `Controller` over the
same durable rows - the unit's restart - and every port call is recorded, so the exercise also
shows the controller's only writes are one D9 decision and the alias CAS: admitted request
pins and consumer balances are not in its reach.

    uv run --frozen pytest -q tests/i/lab_rollout/test_exercise.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors

from ...r.control.test_control import (BASE, BASE_RUN, CAND, CAND_RUN, HORIZON, OPERATOR,
                                       POLICY, POLICY_REF, FakeReleases, FakeServing,
                                       controller, live, plan, report, step)


class Recorded:
    """A port whose every call is logged by name."""

    def __init__(self, port, calls: list[str]) -> None:
        self._port, self._calls = port, calls

    def __getattr__(self, name):
        target = getattr(self._port, name)

        async def call(*args, **kwargs):
            self._calls.append(name)
            return await target(*args, **kwargs)
        return call


def test_i7_the_rollback_exercise_never_expands_and_every_restart_converges() -> None:
    """Failure oracle: stale telemetry after a controller outage that expands or lets an
    operator approve; a breach decided twice across restarts; a partitioned alias that a
    restarted controller leaves on the candidate (or that recovered metrics bring back); an
    emergency rollback that records a second decision; or a controller that writes anything
    but D9's release row and the serving alias."""
    store, serving, calls = FakeReleases(), FakeServing(current=CAND), []
    d9, l3 = Recorded(store, calls), Recorded(serving, calls)

    def restarted():
        return controller(d9, l3)

    # 1. The controller was down; its telemetry is stale. Restarted, it holds and the
    #    operator's approval is refused: an outage never expands traffic.
    stale = live(lag_s=10_000)
    verdict = asyncio.run(step(restarted(), stale, rep=report()))
    assert verdict.action == "hold" and "metrics_stale" in verdict.reasons
    with pytest.raises(errors.StateConflict):
        asyncio.run(restarted().approve(OPERATOR, POLICY, POLICY_REF, plan(), stale,
                                        now=HORIZON, report=report(), runs=(BASE_RUN, CAND_RUN)))
    assert store.row.state == "running" and serving.current == CAND
    # 2. A breach during a partition from the serving control: D9 records the rollback once,
    #    the alias CAS cannot land, the pass fails (the unit exits and systemd restarts it).
    serving.fail = 2
    with pytest.raises(ConnectionError):
        asyncio.run(step(restarted(), live(errors_=21)))
    with pytest.raises(ConnectionError):                    # restarted, still partitioned
        asyncio.run(step(restarted(), live()))
    assert store.row.state == "rolled_back" and serving.current == CAND
    # 3. The partition heals: the next restart converges the alias to the baseline, and
    #    recovered metrics with a winning report never bring the candidate back.
    verdict = asyncio.run(step(restarted(), live(), rep=report()))
    assert verdict.action == "rolled_back" and serving.current == BASE
    assert asyncio.run(step(restarted(), live(), rep=report())).action == "rolled_back"
    # 4. The operator's emergency rollback on a rolled-back release only converges.
    asyncio.run(restarted().emergency_rollback(OPERATOR, POLICY, POLICY_REF, now=HORIZON,
                                               reason="exercise"))
    assert len(store.decisions) == 1 and store.decisions[0][1] == ("error_rate",)
    assert len(serving.rollbacks) == 1 and serving.current == BASE
    assert set(calls) == {"release", "transition", "serving", "rollback"}
    assert calls.count("transition") == 2          # the breach, the refused emergency CAS
