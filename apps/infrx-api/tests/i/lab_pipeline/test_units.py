#!/usr/bin/env python3
"""I6 (LAB-WORKERS): the annotation (teacher) and training worker units are I5's shape -
bounded, OFF until their own env file exists, uncoupled from the consumer - and in addition
refuse to start unless the preflight passes, with every HTTP client's egress sent to a dead
proxy except the preflight-checked allowlist. No docker: the files are read as shipped.

    uv run --frozen pytest -q tests/i/lab_pipeline/test_units.py
"""
from __future__ import annotations

import re
from pathlib import Path

from ..lab_eval.test_units import directive

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
UNITS = API / "deploy" / "lab" / "pipelines"
RUNBOOK = REPO / "infra" / "lab" / "workers" / "training" / "RUNBOOK.md"
ROLES = ("annotation", "training")
CHECKOUT = "/home/ubuntu/model-inference"        # marlin2b-vllm.service's checkout
DEAD = "http://127.0.0.1:9"
EGRESS = tuple(f"-e {name}={value}" for name, value in (
    ("HTTP_PROXY", DEAD), ("http_proxy", DEAD), ("HTTPS_PROXY", DEAD), ("https_proxy", DEAD),
    ("NO_PROXY", "${LAB_EGRESS_ALLOW}"), ("no_proxy", "${LAB_EGRESS_ALLOW}")))


def unit(role: str, units: Path = UNITS) -> str:
    return (units / f"infrx-lab-{role}.service").read_text()


def shape(role: str, text: str, runbook: str) -> str:
    """I5's bounds, OFF-by-default and isolation, plus the preflight and the egress deny, for
    any Lab unit of this lane; returns its health port."""
    env = f"/etc/infrx-lab/{role}.env"
    (start,) = directive(text, "ExecStart")
    assert start.endswith(f"python -m infrx.lab.workers {role}"), start
    for bound in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                  "--cpu-shares 256", f"--name infrx-lab-{role} ", "--user 10003:10000",
                  f"--env-file {env} ", *EGRESS):
        assert bound in start, (role, bound)
    memory = re.search(r"--memory (\S+) --memory-swap (\S+)", start)
    assert memory and memory.group(1) == memory.group(2), (role, "swap past the bound")
    assert re.search(r"--cpus \d", start) and re.search(r"--pids-limit \d+", start)
    assert re.search(r"--tmpfs /tmp:rw,size=\d+[mg],", start), role
    assert directive(text, "ConditionPathExists") == [env]
    assert directive(text, "EnvironmentFile") == [env]
    assert directive(text, "Restart") == ["on-failure"]
    assert directive(text, "StartLimitBurst") and directive(text, "TimeoutStopSec")
    assert directive(text, "Requires") == ["docker.service"]
    assert not directive(text, "PartOf") and not directive(text, "BindsTo")
    for after in directive(text, "After"):
        assert set(after.split()) <= {"docker.service", "network-online.target"}, after
    # The preflight gates the start: no `-` (a failure would be ignored), no `+` (root would
    # run a script the ubuntu account can edit), and it reads this role's own env file.
    assert f"{CHECKOUT}/infra/lab/workers/training/preflight.py --role {role} " \
           f"--env-file {env}" in directive(text, "ExecStartPre")[0]
    assert directive(text, "ExecStartPre")[0].startswith("/usr/bin/python3 ")
    assert f"infrx-lab-{role}.service" in runbook
    port = re.search(r"-e LAB_WORKER_HEALTH_PORT=(\d+)", start).group(1)
    assert f"| {port} |" in runbook, (role, port)
    return port


def test_i6_each_role_is_bounded_off_by_default_and_gated_by_the_preflight() -> None:
    """Failure oracle: a teacher or training worker that is unbounded, starts without its own
    env file, runs as root or the consumer's uid, is coupled to a consumer unit, starts when
    the preflight fails (or runs it as root), or shares a health port with another Lab role."""
    runbook = RUNBOOK.read_text()
    ports = {shape(role, unit(role), runbook) for role in ROLES}
    others = {re.search(r"LAB_WORKER_HEALTH_PORT=(\d+)", p.read_text()).group(1)
              for p in (API / "deploy" / "lab").rglob("*.service") if p.parent != UNITS}
    assert len(ports) == len(ROLES) and not ports & others, (ports, others)


def test_i6_egress_is_denied_by_default_through_a_dead_proxy_the_env_file_cannot_override() -> None:
    """Failure oracle: a proxy flag missing in one letter case (a lowercase `https_proxy=` in
    the env file then switches the deny off, see test_egress.py), a live proxy address, or a
    NO_PROXY that is not the preflight-checked LAB_EGRESS_ALLOW. Port 9 is privileged: no
    unprivileged process can listen there and become the proxy."""
    for role in ROLES:
        (start,) = directive(unit(role), "ExecStart")
        assert re.findall(r"-e (\w+)=", start) == ["LAB_WORKER_HEALTH_PORT", *(
            flag.split()[1].split("=")[0] for flag in EGRESS)], role
