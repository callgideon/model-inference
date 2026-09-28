#!/usr/bin/env python3
"""I5 (LAB-WORKERS): the Lab worker units are bounded, OFF by default and uncoupled from the
consumer. No docker: the files are read as shipped.

    uv run --frozen pytest -q tests/i/lab_eval/test_units.py
"""
from __future__ import annotations

import re
from pathlib import Path

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
UNITS = API / "deploy" / "lab" / "eval"
RUNBOOK = REPO / "infra" / "lab" / "workers" / "eval" / "RUNBOOK.md"
ROLES = ("datasets", "eval", "checkpoints")


def unit(role: str) -> str:
    return (UNITS / f"infrx-lab-{role}.service").read_text()


def directive(text: str, name: str) -> list[str]:
    """Every value of `name=` with continuation lines joined; comments skipped."""
    joined = text.replace("\\\n", " ")
    return [line.split("=", 1)[1].strip() for line in joined.splitlines()
            if line.startswith(f"{name}=")]


def test_i5_each_role_is_its_own_bounded_unit_calling_the_documented_entry_point() -> None:
    """Failure oracle: an unbounded worker (no memory, swap, CPU, pids or scratch bound, a
    writable root, capabilities kept), one that outweighs inference for CPU, a crash loop
    without a limit, two roles on one health port, or a unit running something other than
    the entry point the runbook documents."""
    ports, runbook = set(), RUNBOOK.read_text()
    for role in ROLES:
        text = unit(role)
        (start,) = directive(text, "ExecStart")
        assert start.endswith(f"python -m infrx.lab.workers {role}"), start
        assert "`python -m infrx.lab.workers <role>`" in runbook and \
            f"infrx-lab-{role}.service" in runbook
        for bound in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                      "--cpu-shares 256", f"--name infrx-lab-{role} "):
            assert bound in start, (role, bound)
        memory = re.search(r"--memory (\S+) --memory-swap (\S+)", start)
        assert memory and memory.group(1) == memory.group(2), (role, "swap past the bound")
        assert re.search(r"--cpus \d", start) and re.search(r"--pids-limit \d+", start)
        assert re.search(r"--tmpfs /tmp:rw,size=\d+[mg],", start), role
        assert "--user 10003:10000" in start           # not the consumer worker's 10002
        port = re.search(r"-e LAB_WORKER_HEALTH_PORT=(\d+)", start).group(1)
        ports.add(port)
        assert f"| {port} |" in runbook, (role, port)
        assert directive(text, "Restart") == ["on-failure"]
        assert directive(text, "StartLimitBurst") and directive(text, "StartLimitIntervalSec")
        assert directive(text, "TimeoutStopSec") and "docker stop -t" in directive(
            text, "ExecStop")[0]
    assert len(ports) == len(ROLES)


def test_i5_every_role_is_off_until_its_env_file_exists_and_nothing_installs_it() -> None:
    """Failure oracle: a role that starts without its own env file (the capability flag), one
    reading the gateway's env file (the consumer's secrets), or an installer / rollout step
    that installs, enables or names a Lab unit."""
    for role in ROLES:
        text = unit(role)
        env = f"/etc/infrx-lab/{role}.env"
        assert directive(text, "ConditionPathExists") == [env]
        assert directive(text, "EnvironmentFile") == [env]
        assert f"--env-file {env} " in directive(text, "ExecStart")[0]
        assert "marlin2b-gateway.env" not in "\n".join(
            line for line in text.splitlines() if not line.startswith("#"))
    shipped = [API / "deploy" / "install.sh", API / "deploy" / "release-bundle.sh",
               API / "deploy" / "lib.sh", *sorted((REPO / "infra" / "rollout").rglob("*.sh"))]
    for path in shipped:
        text = path.read_text()
        assert "infrx-lab" not in text and "/lab/eval" not in text, path


def test_i5_no_consumer_unit_depends_on_a_lab_unit_or_the_reverse() -> None:
    """LAB-WORKERS: a Lab outage never blocks inference. Failure oracle: a Lab unit that is
    `PartOf=`/`BindsTo=`/`Requires=` a consumer unit (stopped or restarted with it), ordered
    after one, or a consumer unit that names a Lab unit."""
    for role in ROLES:
        text = unit(role)
        assert not directive(text, "PartOf") and not directive(text, "BindsTo")
        assert directive(text, "Requires") == ["docker.service"]
        for after in directive(text, "After"):
            assert set(after.split()) <= {"docker.service", "network-online.target"}, after
    for consumer in sorted((API / "deploy").glob("*.service")):
        assert "infrx-lab" not in consumer.read_text(), consumer
