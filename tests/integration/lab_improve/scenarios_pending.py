"""E7L i07 (training) and i09: the legs that wait on an unmerged input or an unallocated
target - NOT RUN, never a pass, each naming its lane and the exact rerun. Each case states the
steps it will run once bound; each asserts first that its binding has not landed (so a bind is
loud). i07's annotation half and i08 are bound (`scenarios_surface.py`, composition-4).

* i07 (P-11): the I6 training worker process. The entry module refuses `training` by name
  ("training has no worker pass": the manual bundle has no platform job, and an automatic
  connector is advertised only from a P-11 record); the tripwire fires when a pass lands.
  Bound: start the training worker on an ambiguous automatic run, kill it mid-poll, restart,
  and require one job and one settlement.
* i09 (staging-target): the candidate proposed and approved through L3/L4 into a private or
  allocated staging deployment and its traces pinned to that serving version. No staging
  target is allocated to this key; `INFRX_E7L_STAGING` names one when it is.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    """Loaded under a name unique to this package's own directory: `lab_world` is also every
    sibling lab_*/'s module name (WR-E7L-5) - with two such packages in one process, a bare
    `import lab_world` resolves to whichever package's directory sorts first in sys.path, not
    to the importing file's own package."""
    key = f"{HERE.name}.{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


lw = _sibling("lab_world")


def waits(sid: str, *lanes: str, why: str) -> None:
    """NOT RUN while `lanes` are unmerged, naming the rerun."""
    lw.not_run(sid, *lanes, why=why)


def pass_landed(role: str) -> bool:
    """composition-2's entry module refuses `annotation`/`training` by name until a pass
    exists; the module alone binds nothing."""
    main = lw.API / "infrx" / "lab" / "workers" / "__main__.py"
    return main.exists() and f"{role} has no worker pass" not in main.read_text()


def unbound():
    """Reached only if `lw.not_run` did not skip: an unbound case is never a pass."""
    import pytest
    pytest.fail("E7L-BIND: this case is not bound to the merged lane yet")


def test_i07_the_training_worker_process_never_resubmits():
    steps = ("start `python -m infrx.lab.workers training` on an ambiguous automatic run; "
             "SIGKILL mid-poll; restart; one job at the protocol server, one settlement")
    assert not pass_landed("training"), "a training worker pass landed: bind this case"
    waits("i07", "P-11", why=f"no training worker pass. Steps: {steps}")
    unbound()


def test_i09_the_candidate_is_promoted_through_l3_l4_staging():
    assert not os.environ.get("INFRX_E7L_STAGING"), "a staging target is named: bind this case"
    waits("i09", "staging-target",
               why="no private or allocated staging deployment for key e7l. Steps: register "
                   "candidate 1 through L3, propose/approve it through L4/R2, collect its "
                   "traces pinned to that serving version, then i03 over them")
    unbound()
