"""E6L j09-j10: the journey's legs that wait on unmerged lanes - NOT RUN, never a pass, each
naming its lane and the exact rerun. Each case states the steps it will run once bound.

* j09's checkpoint half (L3, WR-B3-3): the eval worker process is bound
  (`scenarios_workers.py`, composition batch 2); the checkpoints role of
  `python -m infrx.lab.workers` refuses to start until L3's dev deployer and a registry
  adapter exist (without them B3 would reject every checkpoint). Bound, it starts the
  checkpoints worker and drains the `checkpoint_received` outbox twice (a relay redelivery)
  with one run queued. The tripwire: the role composing.
* j10 (B4 + lab-api-2): the provider UI (`apps/lab/app/(provider)/{evaluations,experiments}/`,
  B4, merged on the tip in batch #7) over the gateway's `/lab/v1/evaluations` route
  (`infrx/gateway/routes/lab_evaluations.py`, lab-api-2, codex/w5-lab-api-2), driven against
  this stack's comparison: launch, progress, cancel, and the j07 report shown with its
  slices, uncertainty and missing cases (`apps/lab/tests/e2e/evaluate/`). NOT RUN names
  whichever of the two is absent.
"""
from __future__ import annotations

import lab_world as lw


def checkpoints_bound() -> bool:
    """The checkpoints role composes: it refuses by name until WR-B3-3's sources exist."""
    from infrx.lab.workers import __main__ as workers
    try:
        workers.compose("checkpoints", {"LAB_DATABASE_URL": "postgresql://x@127.0.0.1:9/x",
                                        "LAB_WORKER_HEALTH_PORT": "9"})
    except ValueError as refused:
        return "WR-B3-3" not in str(refused)
    return True


def waits(sid: str, *lanes: str, why: str) -> None:
    """NOT RUN while `lanes` are unmerged, naming the rerun."""
    lw.not_run(sid, *lanes, why=why)


def unbound():
    """Reached only if `waits` did not skip: an unbound case is never a pass."""
    import pytest
    pytest.fail("E6L-BIND: this case is not bound to the merged lane yet")


def test_j09_the_checkpoint_worker_drains_the_outbox_once():
    steps = ("start the checkpoints worker; deliver one checkpoint_received event twice "
             "(release, redeliver); one receipt, one run, one eval_run event")
    assert not checkpoints_bound(), "the checkpoints role composes: bind this case"
    waits("j09", "L3", why=f"the checkpoints role refuses until L3's dev deployer and a "
                           f"registry adapter exist (WR-B3-3). Steps: {steps}")
    unbound()


def test_j10_the_provider_ui_launches_compares_and_cancels():
    parts = {"B4": lw.REPO / "apps" / "lab" / "app" / "(provider)" / "evaluations",
             "lab-api-2": lw.API / "infrx" / "gateway" / "routes" / "lab_evaluations.py"}
    absent = [lane for lane, path in parts.items() if not path.exists()]
    assert absent, "B4 and lab-api-2 landed: bind apps/lab/tests/e2e/evaluate/"
    waits("j10", *absent, why="the provider evaluation UI or its /lab/v1/evaluations route "
                              "is not on this base. Steps: launch, progress, cancel and the "
                              "j07 comparison through apps/lab/tests/e2e/evaluate/")
    unbound()
