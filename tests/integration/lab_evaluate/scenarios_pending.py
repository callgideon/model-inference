"""E6L j10: the journey's leg that waits on unmerged lanes - NOT RUN, never a pass, naming
its lane and the exact rerun. The case states the steps it will run once bound. (j09's
checkpoint half is bound in `scenarios_workers.py`, composition batch 5, WR-B3-3.)

* j10 (lab-e2e, R222's "lab-e2e UI" class): the provider UI (`apps/lab/app/(provider)/
  {evaluations,experiments}/`, B4) over the gateway's `/lab/v1/evaluations` route
  (`infrx/gateway/routes/lab_evaluations.py`, lab-api-2), both merged, driven against this
  stack's comparison: launch, progress, cancel, and the j07 report shown with its slices,
  uncertainty and missing cases. The browser leg is the lab-e2e lane's harness
  (`apps/lab/tests/e2e/evaluate/`); NOT RUN until it lands, then this case must be bound.
"""
from __future__ import annotations

import importlib.util
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


def unbound():
    """Reached only if `waits` did not skip: an unbound case is never a pass."""
    import pytest
    pytest.fail("E6L-BIND: this case is not bound to the merged lane yet")


def test_j10_the_provider_ui_launches_compares_and_cancels():
    suite = lw.REPO / "apps" / "lab" / "tests" / "e2e" / "evaluate"
    assert not suite.exists(), "lab-e2e landed: bind j10 to apps/lab/tests/e2e/evaluate/"
    waits("j10", "lab-e2e", why="the provider evaluation UI (B4) over /lab/v1/evaluations "
                                "(lab-api-2) runs in the Lab web, which this runner does not "
                                "start; its browser leg is the lab-e2e harness "
                                "(apps/lab/tests/e2e/evaluate/, `make lab-e2e`): launch, "
                                "progress, cancel and the j07 comparison")
    unbound()
