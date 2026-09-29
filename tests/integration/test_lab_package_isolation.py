"""WR-E7L-5 seam: `pytest tests/integration` collects all five lab_* packages (evaluate,
observe, operate, rollout, improve) in one session. Each owns same-named helper modules
(runner.py, mutants.py, lab_world.py/observe_world.py, scenarios_pending.py) that imported
their own package-local sibling by bare name after `sys.path.insert(0, str(HERE))`. Once two
or more packages are collected in one process, whichever package's directory was inserted
last sits first in `sys.path`, and a bare `import lab_world`/`import observe_world` anywhere
in the process resolves there - not to the importing file's own package - and stays cached for
the rest of the process. The proven failure: lab_rollout's runner-identity case reported
lab_improve's `RUNNER` path (merge #21, WR-E7L-5). Run alone, each package is unaffected; run
together, the seam broke. Fixed by loading every such sibling under a name unique to the
importing package (`tests/integration/lab_*/_sibling.py`) instead of a bare module name.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / "apps" / "infrx-api" / ".venv" / "bin" / "python"

#: one runner-identity case per package: each proves its OWN rerun message, never a sibling's.
CASES = (
    "lab_operate/test_e3l_runner.py::test_e3l_a_not_run_case_names_its_lanes_and_the_exact_rerun",
    "lab_observe/test_e5l_runner.py::test_e5l_a_not_run_case_names_its_lanes_and_the_exact_rerun",
    "lab_evaluate/test_e6l_runner.py::test_e6l_a_not_run_case_names_its_lanes_and_the_exact_rerun",
    "lab_improve/test_e7l_runner.py::test_e7l_a_not_run_case_names_its_lanes_and_the_exact_rerun",
    "lab_improve/test_e7l_runner.py::test_e7l_the_i07_tripwire_fires_on_a_worker_pass_not_the_module",
    "lab_rollout/test_e8l_runner.py::test_e8l_a_not_run_case_names_its_lanes_and_the_exact_rerun",
)


def _run(*targets: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", "-q", *(f"tests/integration/{t}" for t in targets)],
        cwd=REPO, capture_output=True, text=True, timeout=120)


def test_every_packages_runner_identity_case_passes_when_all_five_are_collected_together():
    """Red before the fix: lab_rollout's and lab_improve's cases fail with a rerun message
    naming a sibling package's runner.py, once every package is in the one session."""
    result = _run(*CASES)
    assert result.returncode == 0, result.stdout[-4000:] + "\n" + result.stderr[-2000:]


def test_each_package_alone_still_passes_its_own_case():
    """Collected one at a time (no sibling package in the process), the seam never fires - the
    regression is only ever cross-package, and the fix must not need every package present."""
    for case in CASES:
        result = _run(case)
        assert result.returncode == 0, f"{case}: {result.stdout[-2000:]}"
