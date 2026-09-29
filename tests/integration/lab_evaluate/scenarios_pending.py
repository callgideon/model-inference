"""E6L j09: the journey's leg that waits on unmerged lanes - NOT RUN, never a pass, naming its
lane and the exact rerun; the case states the steps it will run once bound. (j10, the provider
UI, is bound in `scenarios_eval.py` to `apps/lab/tests/e2e/evaluate`, LAB-E2E.)

* j09's checkpoint half (L3, WR-B3-3): the eval worker process is bound
  (`scenarios_workers.py`, composition batch 2); the checkpoints role of
  `python -m infrx.lab.workers` refuses to start until L3's dev deployer and a registry
  adapter exist (without them B3 would reject every checkpoint). Bound, it starts the
  checkpoints worker and drains the `checkpoint_received` outbox twice (a relay redelivery)
  with one run queued. The tripwire: the role composing.
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
