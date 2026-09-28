#!/usr/bin/env python3
"""I6 (LAB-WORKERS): the annotation (teacher) and training worker units are I5's shape -
bounded, OFF until their own env file exists, uncoupled from the consumer - and in addition
refuse to start unless the preflight passes, with every HTTP client's egress sent to a dead
proxy except the preflight-checked allowlist. No docker: the files are read as shipped.

    uv run --frozen pytest -q tests/i/lab_pipeline/test_units.py
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..lab_eval.test_units import directive

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
UNITS = API / "deploy" / "lab" / "pipelines"
RUNBOOK = REPO / "infra" / "lab" / "workers" / "training" / "RUNBOOK.md"
ROLES = ("annotation", "training")
CHECKOUT = "/home/ubuntu/model-inference"        # marlin2b-vllm.service's checkout
ENABLED = "/etc/infrx-lab/enabled"               # I2L's enable marker (infra/lab/app/lab.json)
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
                  "--cpu-shares 256", f"--name infrx-lab-{role} ", "--user 10003:10003",
                  f"--env-file {env} ", *EGRESS):
        assert bound in start, (role, bound)
    memory = re.search(r"--memory (\S+) --memory-swap (\S+)", start)
    assert memory and memory.group(1) == memory.group(2), (role, "swap past the bound")
    assert re.search(r"--cpus \d", start) and re.search(r"--pids-limit \d+", start)
    assert re.search(r"--tmpfs /tmp:rw,size=\d+[mg],", start), role
    assert directive(text, "ConditionPathExists") == [ENABLED, env]      # both, ANDed
    assert directive(text, "EnvironmentFile") == [env]
    assert directive(text, "Restart") == ["on-failure"]
    assert directive(text, "StartLimitBurst") and directive(text, "TimeoutStopSec")
    assert directive(text, "Requires") == ["docker.service"]
    assert not directive(text, "PartOf") and not directive(text, "BindsTo")
    for after in directive(text, "After"):
        assert set(after.split()) <= {"docker.service", "network-online.target"}, after
    # The preflight gates the start: no `-` (a failure would be ignored), no `+` (root would
    # run a script the ubuntu account can edit), isolated (`-I`: EnvironmentFile= reaches
    # ExecStartPre too, so a PYTHON* setting must not steer the checker), and it reads this
    # role's own env file.
    assert directive(text, "ExecStartPre")[0] == (
        f"/usr/bin/python3 -I {CHECKOUT}/infra/lab/workers/training/preflight.py "
        f"--role {role} --env-file {env}")
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
    others = {port for p in (API / "deploy" / "lab").rglob("*.service") if p.parent != UNITS
              for port in re.findall(r"LAB_WORKER_HEALTH_PORT=(\d+)", p.read_text())}
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


# `docker run` flags a Lab unit may carry; values fixed where one value is the only safe one.
FLAGS = {"--rm", "--init", "--pull", "--name", "--network", "--user", "--read-only", "--tmpfs",
         "--cap-drop", "--security-opt", "--memory", "--memory-swap", "--cpus", "--cpu-shares",
         "--pids-limit", "--env-file", "-e"}
FIXED = {"--pull": ["never"], "--network": ["host"], "--user": ["10003:10003"],
         "--cap-drop": ["ALL"], "--security-opt": ["no-new-privileges"]}
EXPANDED = {"INFRX_IMAGE", "LAB_EGRESS_ALLOW"}     # the only ${NAME}s: neither is a secret


def hardening(role: str, text: str) -> None:
    """The unit's argv, parsed as docker parses it: exactly FLAGS (any other flag - a mount,
    a device, an added capability, a host namespace, `--privileged`, a seccomp or AppArmor
    opt-out, a DNS or hosts override, a group - fails), FIXED values, a tmpfs docker's
    noexec/nosuid/nodev defaults still apply to, the image then exactly the entry point, and
    no expansion of a setting that could be a secret (argv is readable in `ps`)."""
    (start,) = directive(text, "ExecStart")
    words, flags, i = shlex.split(start), {}, 2
    assert words[:2] == ["/usr/bin/docker", "run"], words[:2]
    while words[i] != "${INFRX_IMAGE}":
        if "=" in words[i] or words[i] in ("--rm", "--init", "--read-only"):   # --pid=host
            name, _, value = words[i].partition("=")
            flags.setdefault(name, []).append(value or None)
            i += 1
        else:
            flags.setdefault(words[i], []).append(words[i + 1])
            i += 2
    assert set(flags) == FLAGS, set(flags) ^ FLAGS
    assert {f: flags[f] for f in FIXED} == FIXED, role
    (tmpfs,) = flags["--tmpfs"]
    assert not {"exec", "suid", "dev"} & set(tmpfs.split(":", 1)[1].split(",")), tmpfs
    assert words[i + 1:] == ["python", "-m", "infrx.lab.workers", role]
    exec_lines = " ".join(directive(text, "ExecStart") + directive(text, "ExecStartPre"))
    assert set(re.findall(r"\$\{?(\w+)", exec_lines)) == EXPANDED, role


def test_i6_argv_carries_no_mount_privilege_namespace_pull_or_secret() -> None:
    """The security lens on the unit itself. Failure oracle: a Lab worker unit that mounts a
    host path or the docker socket, adds a capability, device or privilege, joins a host
    namespace, opts out of seccomp/AppArmor, lets the daemon pull (registry egress outside
    the container's deny proxy), runs in the consumer runtime's group 10000, makes its
    scratch space executable, or puts a token or DSN on the command line."""
    for role in ROLES:
        hardening(role, unit(role))
