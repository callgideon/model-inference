#!/usr/bin/env python3
"""I7 (LAB-WORKERS, ROLLOUT-RECOVER): the rollout controller unit is I6's shape (bounded, OFF
until its env file exists, gated by the preflight, egress denied) with no adapter, no spend and
no way to buy capacity, and its own readiness port. No docker: the files are read as shipped.

    uv run --frozen pytest -q tests/i/lab_rollout/test_units.py
"""
from __future__ import annotations

import re
from pathlib import Path

from ..lab_eval.test_units import directive
from ..lab_pipeline.test_units import shape, unit

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
UNITS = API / "deploy" / "lab" / "rollout"
RUNBOOK = REPO / "infra" / "lab" / "workers" / "rollout" / "RUNBOOK.md"


def test_i7_the_controller_is_bounded_off_by_default_gated_and_independently_ready() -> None:
    """Failure oracle: a controller that is unbounded, starts without its env file or when the
    preflight refuses it, is coupled to a consumer unit (a controller restart restarting
    inference, or the reverse), or answers readiness on a port another Lab role uses."""
    text, runbook = unit("rollout", UNITS), RUNBOOK.read_text()
    port = shape("rollout", text, runbook)
    others = {port for p in (API / "deploy" / "lab").rglob("*.service") if p.parent != UNITS
              for port in re.findall(r"LAB_WORKER_HEALTH_PORT=(\d+)", p.read_text())}
    assert port not in others, (port, others)
    assert "/readyz" in runbook and "emergency-rollback" in runbook


def test_i7_the_controller_cannot_buy_capacity_or_reach_past_the_object_store() -> None:
    """No automatic capacity purchases. Failure oracle: the docker socket or any host path
    mounted (a controller that can start containers or read host credentials), added
    capabilities or privileges, or a host PID/IPC namespace."""
    (start,) = directive(unit("rollout", UNITS), "ExecStart")
    assert "docker.sock" not in start
    assert not re.search(r" (-v|--volume|--mount|--privileged|--cap-add|--pid|--ipc|--device)"
                         r"[ =]", start), start
