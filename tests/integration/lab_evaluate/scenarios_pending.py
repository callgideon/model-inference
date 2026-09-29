"""E6L j10: the journey's leg that waits on unmerged lanes - NOT RUN, never a pass, naming
its lane and the exact rerun. The case states the steps it will run once bound. (j09's
checkpoint half is bound in `scenarios_workers.py`, composition batch 5, WR-B3-3.)

* j10 (B4 + lab-api-2): the provider UI (`apps/lab/app/(provider)/{evaluations,experiments}/`,
  B4, merged on the tip in batch #7) over the gateway's `/lab/v1/evaluations` route
  (`infrx/gateway/routes/lab_evaluations.py`, lab-api-2, codex/w5-lab-api-2), driven against
  this stack's comparison: launch, progress, cancel, and the j07 report shown with its
  slices, uncertainty and missing cases (`apps/lab/tests/e2e/evaluate/`). NOT RUN names
  whichever of the two is absent.
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
    parts = {"B4": lw.REPO / "apps" / "lab" / "app" / "(provider)" / "evaluations",
             "lab-api-2": lw.API / "infrx" / "gateway" / "routes" / "lab_evaluations.py"}
    absent = [lane for lane, path in parts.items() if not path.exists()]
    assert absent, "B4 and lab-api-2 landed: bind apps/lab/tests/e2e/evaluate/"
    waits("j10", *absent, why="the provider evaluation UI or its /lab/v1/evaluations route "
                              "is not on this base. Steps: launch, progress, cancel and the "
                              "j07 comparison through apps/lab/tests/e2e/evaluate/")
    unbound()
