#!/usr/bin/env python3
"""I2B-R4: the worker unit and what probes it agree on one port and one entry point.

    uv run --frozen pytest -q tests/i/test_worker_unit.py

`deploy/rehearse.sh` step 4b starts the unit for real (the installed unit file, the runtime
image, the dev env file) and reads `/readyz` and `/metrics` on the port checked here.
"""
from __future__ import annotations

import re

from infrx.config import DEPLOYMENT_DEFAULTS, deployment_from_env

from . import support
from .support import preflight

DEPLOY = support.API_DIR / "deploy"
VERIFY = support.REPO / "infra" / "rollout" / "steps" / "60-verify-local.sh"


def test_backend_deploy__the_worker_readiness_port_is_one_value_the_installer_never_writes():
    """The port `python -m infrx.worker` binds (`WORKER_HEALTH_PORT`, 08 §5.1) is the one
    install.sh's `wait_ready` and the rollout's 60-verify-local.sh probe; the runtime reads
    the name, and the installer refuses to write it (a `--set` would move the listener and
    leave both probes on the default)."""
    port = DEPLOYMENT_DEFAULTS.worker_health_port
    assert port == 8002 and deployment_from_env({"WORKER_HEALTH_PORT": "9002"}) \
        .worker_health_port == 9002
    probed = f"${{WORKER_HEALTH_PORT:-{port}}}"
    assert f"WORKER_READY=http://127.0.0.1:{probed}/readyz" in (DEPLOY / "lib.sh").read_text()
    assert f'"http://127.0.0.1:{probed}/readyz"' in VERIFY.read_text()
    assert "WORKER_HEALTH_PORT" in preflight.NOT_SETTABLE
    problems = preflight.tunables(("WORKER_HEALTH_PORT=9002",), {})
    assert problems and problems[0].startswith("--set WORKER_HEALTH_PORT: not a tunable"), \
        problems
    unit = (DEPLOY / "infrx-worker.service").read_text()
    assert re.search(r"--network host .*python -m infrx\.worker\n", unit.replace("\\\n", " "))
