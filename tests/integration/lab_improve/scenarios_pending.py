"""E7L i07-i09: the legs that wait on unmerged lanes or unallocated targets - NOT RUN, never a
pass, each naming its lane and the exact rerun. Each case states the steps it will run once
bound; each asserts first that its binding has not landed (so a merge fails it loudly).

* i07 (composition-2): the I6 annotation and training worker processes
  (`deploy/lab/pipelines/*.service` run `python -m infrx.lab.workers <role>`); the entry
  module is composition-2's, not on this base. Bound: start the annotation worker against
  this stack's Lab database, SIGKILL it between a batch's chunks, restart it, and require one
  teacher job per chunk; start the training worker on an ambiguous automatic run, kill it
  mid-poll, restart, and require one job and one settlement.
* i08 (LAB_PIPELINES + P3-evaluations): the provider UI (P4, `apps/lab/app/(provider)/
  {annotations,training}/`) over `/lab/v1/pipelines`, whose composition (`pilot.py`'s
  `LabPipelines`) carries the store only: the label log, run ledger and P3's evaluation port
  answer 503 until composed (WR-E7L-1). Bound: import labels, review, export, prepare,
  submit, upload a checkpoint and approve it through the UI (`apps/lab/tests/e2e/improve/`).
* i09 (staging-target): the candidate proposed and approved through L3/L4 into a private or
  allocated staging deployment and its traces pinned to that serving version. No staging
  target is allocated to this key; `INFRX_E7L_STAGING` names one when it is.
"""
from __future__ import annotations

import os
import re

import lab_world as lw


def waits(sid: str, *lanes: str, why: str) -> None:
    """NOT RUN while `lanes` are unmerged, naming the rerun."""
    lw.not_run(sid, *lanes, why=why)


def unbound():
    """Reached only if `lw.not_run` did not skip: an unbound case is never a pass."""
    import pytest
    pytest.fail("E7L-BIND: this case is not bound to the merged lane yet")


def test_i07_the_annotation_worker_process_resumes_a_batch_once():
    steps = ("start `python -m infrx.lab.workers annotation` on DATABASE; SIGKILL between "
             "chunks; restart; one teacher job per chunk, labels imported once")
    assert not (lw.API / "infrx" / "lab" / "workers" / "__main__.py").exists(), \
        "the worker entry point landed: bind this case"
    waits("i07", "composition-2", why=f"no I6 worker entry point on this base. Steps: "
                                           f"{steps}")
    unbound()


def test_i07_the_training_worker_process_never_resubmits():
    steps = ("start `python -m infrx.lab.workers training` on an ambiguous automatic run; "
             "SIGKILL mid-poll; restart; one job at the protocol server, one settlement")
    assert not (lw.API / "infrx" / "lab" / "workers" / "__main__.py").exists(), \
        "the worker entry point landed: bind this case"
    waits("i07", "composition-2", why=f"no I6 worker entry point on this base. Steps: "
                                           f"{steps}")
    unbound()


def test_i08_the_provider_ui_drives_labels_to_an_eligible_candidate():
    pilot = (lw.API / "infrx" / "gateway" / "pilot.py").read_text()
    assert not re.search(r"LabPipelines\([^)]*evals", pilot), \
        "LAB_PIPELINES composes P3's evaluation port: bind apps/lab/tests/e2e/improve/"
    waits("i08", "LAB_PIPELINES", "P3-evaluations",
               why="`/lab/v1/pipelines` is composed with the store only (label log, run "
                   "ledger and P3's evaluation port answer 503). Steps: labels, review, "
                   "export, bundle, checkpoint and approve through apps/lab/tests/e2e/improve/")
    unbound()


def test_i09_the_candidate_is_promoted_through_l3_l4_staging():
    assert not os.environ.get("INFRX_E7L_STAGING"), "a staging target is named: bind this case"
    waits("i09", "staging-target",
               why="no private or allocated staging deployment for key e7l. Steps: register "
                   "candidate 1 through L3, propose/approve it through L4/R2, collect its "
                   "traces pinned to that serving version, then i03 over them")
    unbound()
