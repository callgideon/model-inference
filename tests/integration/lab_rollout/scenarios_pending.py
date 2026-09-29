"""E8L k08, k10: the legs that wait - NOT RUN, never a pass, each naming what it waits on and
the exact rerun. Each case states the steps it will run once bound. (k09's I7 entry point
landed with composition-2: its cases live in `scenarios_recover.py` - the emergency-rollback
subcommand and, since composition-5's WR-R2-3, the bare rollout role's pass loop.)

* k08 (P-08): R3 parity on an allocated supported target. This host has no GPU and no Lab
  window is allocated; the only measured serving pair is Marlin-2B on the g6e.2xlarge L40S
  (I7 RUNBOOK section 6). Bound: on the allocated target run
  `./models/marlin2b/serve.sh` for the base and the variant, `models/marlin2b/bench.py` under
  one load profile for each into `marlin2b/results/R3-parity-<variant>/` on the experiment
  branch (the directory's other E1B/E4B/M4 results are not a variant pair), commit, then
  k07's compare with those two load records (`<exp>/results/...@<sha>`).
* k10 (lab-ui-swap): the Lab releases UI (`apps/lab/app/(provider)/releases`) over the real
  `/lab/v1/releases` route, driven by a coordinator-assigned e2e suite
  (`apps/lab/tests/e2e/rollout/`): the R2 verdict shown, an expansion proposed at the fence,
  the operator's approval, an emergency rollback.
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


def test_k10_the_releases_ui_over_the_real_route():
    """lab-ui-swap (#17) landed `apps/lab/app/(provider)/releases` and WR-R4-1's
    `/lab/v1/releases` route mount (LAB_RELEASES, off), but `pilot._lab_2` composes
    `LabReleases(sessions, access)` with no records/proposal port (WR-R4-1's lab-sql half,
    WR-R4-2): every read and the one write answer 503 today, so the route exists but has
    nothing real to show yet - and no coordinator-assigned e2e suite drives it. The store's
    listing the records port will read (0048's `lab_releases_in`) is bound for real in
    `scenarios_route`'s k10 read-half case."""
    suite = lw.REPO / "apps" / "lab" / "tests" / "e2e" / "rollout"
    releases_route = lw.API / "infrx" / "gateway" / "routes" / "lab_releases.py"
    assert releases_route.exists(), "WR-R4-1's route module is gone: re-check this tripwire"
    assert not suite.exists(), "the releases e2e suite landed: bind this case"
    waits("k10", "lab-ui-swap", why="/lab/v1/releases is mounted (LAB_RELEASES, off) but "
                                    "pilot._lab_2 composes LabReleases with no records or "
                                    "proposal port (WR-R4-1 lab-sql half, WR-R4-2): every "
                                    "call answers 503, and apps/lab/tests/e2e/rollout/ (the "
                                    "coordinator-assigned suite this scenario drives) does "
                                    "not exist on this base. Steps: land the read models and "
                                    "proposal store, then verdict, proposal at the fence, "
                                    "approval, emergency rollback through "
                                    "apps/lab/tests/e2e/rollout/")
    unbound()
