"""E6L j09-j10: the journey's legs that wait on unmerged lanes - NOT RUN, never a pass, each
naming its lane and the exact rerun. Each case states the steps it will run once bound.

* j09 (composition): the I5 eval/checkpoint worker processes. The entry point the units run
  (`deploy/lab/eval/*.service`, WR-B-5) is the composition lane's (`infrx/worker/__main__.py`,
  `LAB_EVAL_WORKER`, codex/w5-composition c241ca81), not on this base; that branch has no
  `checkpoint_received` role yet. Bound, it starts the eval worker against this stack's Lab
  database with `LAB_EVAL_WORKER` on,
  lets it lease j05's run, SIGKILLs it mid-attempt, restarts it and requires every case
  scored once; and drains the `checkpoint_received` outbox twice (a relay redelivery) with
  one run queued.
* j10 (B4 + lab-api-2): the provider UI (`apps/lab/app/(provider)/{evaluations,experiments}/`,
  B4, merged on the tip in batch #7) over the gateway's `/lab/v1/evaluations` route
  (`infrx/gateway/routes/lab_evaluations.py`, lab-api-2, codex/w5-lab-api-2), driven against
  this stack's comparison: launch, progress, cancel, and the j07 report shown with its
  slices, uncertainty and missing cases (`apps/lab/tests/e2e/evaluate/`). NOT RUN names
  whichever of the two is absent.
"""
from __future__ import annotations

import lab_world as lw


def bound(setting: str) -> bool:
    """The worker main composes the Lab role named by `setting`."""
    return setting in (lw.API / "infrx" / "worker" / "__main__.py").read_text()


def waits(sid: str, *lanes: str, why: str) -> None:
    """NOT RUN while `lanes` are unmerged, naming the rerun."""
    lw.not_run(sid, *lanes, why=why)


def unbound():
    """Reached only if `waits` did not skip: an unbound case is never a pass."""
    import pytest
    pytest.fail("E6L-BIND: this case is not bound to the merged lane yet")


def test_j09_the_eval_worker_process_killed_mid_run_loses_nothing():
    steps = ("start the eval worker entry point on DATABASE; SIGKILL it mid-attempt; restart; "
             "every case of the run scored once, the killed attempt expired")
    assert not bound("LAB_EVAL_WORKER"), "the entry point landed: bind this case"
    waits("j09", "composition",
          why=f"no I5 eval worker entry point on this base. Steps: {steps}")
    unbound()


def test_j09_the_checkpoint_worker_drains_the_outbox_once():
    steps = ("start the checkpoints worker; deliver one checkpoint_received event twice "
             "(release, redeliver); one receipt, one run, one eval_run event")
    assert not bound("checkpoint_received"), "the entry point landed: bind this case"
    waits("j09", "composition", why=f"no I5 checkpoint worker entry point on this base. "
                                f"Steps: {steps}")
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
