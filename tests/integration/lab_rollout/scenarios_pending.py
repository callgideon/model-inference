"""E8L k08: the leg that waits - NOT RUN, never a pass, naming what it waits on and the exact
rerun. (k10's UI half is bound in `scenarios_route.py` to `apps/lab/tests/e2e/rollout`, LAB-E2E.)
Each case states the steps it will run once bound. (k09's I7 entry point
landed with composition-2: its cases live in `scenarios_recover.py` - the emergency-rollback
subcommand and, since composition-5's WR-R2-3, the bare rollout role's pass loop.)

* k08 (P-08): R3 parity on an allocated supported target. This host has no GPU and no Lab
  window is allocated; the only measured serving pair is Marlin-2B on the g6e.2xlarge L40S
  (I7 RUNBOOK section 6). Bound: on the allocated target run
  `./models/marlin2b/serve.sh` for the base and the variant, `models/marlin2b/bench.py` under
  one load profile for each into `marlin2b/results/R3-parity-<variant>/` on the experiment
  branch (the directory's other E1B/E4B/M4 results are not a variant pair), commit, then
  k07's compare with those two load records (`<exp>/results/...@<sha>`).
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
    pytest.fail("E8L-BIND: this case is not bound to the merged lane yet")


def test_k08_parity_on_an_allocated_gpu_target():
    parity = sorted((lw.REPO / "models" / "marlin2b" / "results").glob("R3-parity*"))
    assert not parity, f"measured parity landed ({parity}): bind this case"
    waits("k08", "P-08", why="no allocated GPU target and no measured results on this base. "
                             "Steps: serve.sh + bench.py for base and variant on the target, "
                             "commit marlin2b/results/R3-parity-*, then k07 with those loads")
    unbound()
