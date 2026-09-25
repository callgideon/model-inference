#!/usr/bin/env python3
"""E2C: does this host have what a verification profile needs? Prints one JSON verdict.

    apps/infrx-api/.venv/bin/python tests/integration/preflight.py consumer-local
    apps/infrx-api/.venv/bin/python tests/integration/preflight.py backend-certify \\
        --certify-profile <profile.json>

Exit 0 = every prerequisite present (PASS), 3 = something is missing, busy or unreachable
(BLOCKED), 4 = the certify profile is invalid (INVALID), 2 = usage. Pins, profiles and the
namespace allocation table are data in `environment.json` (ENVIRONMENT.md explains them).
Nothing here starts, stops or removes anything: a missing prerequisite is reported, never
provisioned, and never counted as a pass.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness                                           # noqa: E402

REPO = harness.REPO_ROOT       # the checkout; INFRX_E2_REPO_ROOT inside a mutant copy
ENVIRONMENT = HERE / "environment.json"
PASS, BLOCKED, INVALID = "PASS", "BLOCKED", "INVALID"
EXIT = {PASS: 0, BLOCKED: 3, INVALID: 4}
# A certify profile references protected material by id/hash; a literal secret is refused.
SECRET_KEY = re.compile(r"secret|password|passwd|token|api_?key|dsn|credential", re.I)


def load(path: Path = ENVIRONMENT) -> dict:
    return json.loads(path.read_text())


def check_tool(name: str, spec: dict) -> dict:
    argv = list(spec["argv"])
    exe = REPO / argv[0] if "/" in argv[0] else shutil.which(argv[0])
    if exe is None or not Path(exe).exists():
        return {"check": f"tool:{name}", "status": "missing", "want": spec["match"],
                "detail": f"{argv[0]} not found"}
    try:
        done = subprocess.run([str(exe), *argv[1:]], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"check": f"tool:{name}", "status": "missing", "want": spec["match"],
                "detail": f"{type(exc).__name__}: {exc}"}
    found = (done.stdout or done.stderr).strip().splitlines()
    first = found[0] if found else ""
    ok = done.returncode == 0 and re.search(spec["match"], done.stdout + done.stderr)
    return {"check": f"tool:{name}", "status": "ok" if ok else "mismatch" if done.returncode == 0
            else "missing", "want": spec["match"], "found": first,
            **({} if done.returncode == 0 else {"detail": f"exit {done.returncode}"})}


def check_image(name: str, spec: dict, docker_ok: bool) -> dict:
    row = {"check": f"image:{name}", "want": spec["ref"]}
    if not docker_ok:
        return {**row, "status": "missing", "detail": "docker unavailable: cannot inspect"}
    done = subprocess.run(["docker", "image", "inspect", spec["ref"]], capture_output=True,
                          timeout=30)
    return {**row, "status": "ok" if done.returncode == 0 else "missing",
            **({} if done.returncode == 0 else {"detail": f"docker pull {spec['ref']}"})}


def port_free(port: int) -> bool:
    """Bindable as a server binds it: SO_REUSEADDR, so a closed connection lingering in
    TIME_WAIT is not "busy" (it made one run BLOCKED spuriously); a listener or a live
    connection's source port still is."""
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def namespace(spec: dict) -> tuple[list[int], list[str]]:
    """(host ports, container-name prefixes). Service namespaces are read from their one
    registry - `infrx/contracts/tasklocal.py`, or the E2 harness for a compose namespace -
    never copied here."""
    if "tasklocal" in spec:
        if str(REPO / "apps" / "infrx-api") not in sys.path:
            sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
        from infrx.contracts.tasklocal import local_services
        chosen = [local_services(spec["tasklocal"])[s] for s in spec["services"]]
        return [s.host_port for s in chosen], [s.container for s in chosen]
    if "harness" in spec:
        import harness
        ports = set(harness.ports_for(spec["harness"]).values())
        if spec.get("postgrest"):     # layer 3: backend/stack.py's PostgREST pair, same offset
            if str(HERE / "backend") not in sys.path:
                sys.path.insert(0, str(HERE / "backend"))
            import stack
            start = harness.range_for(spec["harness"]).start
            ports |= {start + port - harness.PORT_RANGE.start
                      for port in (stack.POSTGREST_PORT, stack.JOURNEY_POSTGREST_PORT)}
        return sorted(ports), [f"infrx-{spec['harness']}-"]
    return spec["ports"], [spec["container_prefix"]]


def check_namespace(name: str, spec: dict, containers: list[str] | None) -> dict:
    ports, prefixes = namespace(spec)
    busy = [port for port in ports if not port_free(port)]
    stale = [c for c in containers or [] if c.startswith(tuple(prefixes))]
    holders = {port: holder(port) for port in busy} if containers is not None else {}
    return {"check": f"namespace:{name}", "status": "busy" if busy or stale else "ok",
            "ports": ports, **({"busy_ports": busy} if busy else {}),
            **({"held_by": holders} if holders else {}),
            **({"existing_containers": stale,
                "detail": "another run (or a crashed one) holds this namespace"} if stale else {})}


def holder(port: int) -> str:
    """Which container publishes a busy port, for the report; '' = not a container."""
    done = subprocess.run(["docker", "ps", "--filter", f"publish={port}", "--format",
                           "{{.Names}}"], capture_output=True, text=True, timeout=30)
    return done.stdout.strip()


def ephemeral_overlap(ports: list[int], proc: Path = Path("/proc/sys/net/ipv4")) -> dict:
    """Namespace ports the kernel may hand out as a connection's source port. A collision
    only ever turns a run into a spurious BLOCKED/FAIL (the bind fails), never a false
    PASS, so this is reported as a risk and does not change the verdict."""
    row = {"check": "ephemeral-overlap"}
    try:
        low, high = map(int, (proc / "ip_local_port_range").read_text().split())
        reserved_text = (proc / "ip_local_reserved_ports").read_text().strip()
    except (OSError, ValueError):
        return {**row, "status": "ok", "detail": "no Linux port-range sysctl on this host"}
    reserved = set()
    for part in filter(None, reserved_text.split(",")):
        first, _, last = part.partition("-")
        reserved.update(range(int(first), int(last or first) + 1))
    exposed = sorted(p for p in set(ports) if low <= p <= high and p not in reserved)
    if not exposed:
        return {**row, "status": "ok"}
    return {**row, "status": "risk", "ports": exposed, "ephemeral_range": [low, high],
            "detail": "a namespace port can be taken as an outgoing source port and the "
                      "container bind then fails; reserve them host-wide, e.g. sysctl -w "
                      "net.ipv4.ip_local_reserved_ports=55400-55999,56700-56999"}


def containers() -> list[str] | None:
    try:
        done = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.split() if done.returncode == 0 else None


def validate_certify_profile(path: Path, required: list[str]) -> dict:
    """Shape only: every required field group present and non-empty, and no literal secret.
    E1C owns the full schema; this refuses what is certainly not a profile."""
    row = {"check": "certify-profile", "path": str(path)}
    try:
        profile = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {**row, "status": "invalid", "detail": f"{type(exc).__name__}: {exc}"}
    if not isinstance(profile, dict):
        return {**row, "status": "invalid", "detail": "not a JSON object"}
    missing = [group for group in required if not profile.get(group)]
    secrets = sorted(_secret_paths(profile))
    problems = ([f"missing or empty field groups: {missing}"] if missing else []) + \
        ([f"literal secrets (reference them by id/hash): {secrets}"] if secrets else [])
    return {**row, "status": "invalid" if problems else "ok",
            **({"detail": "; ".join(problems)} if problems else {})}


def _secret_paths(node, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else key
            if SECRET_KEY.search(key) and isinstance(value, str) and value:
                yield here
            yield from _secret_paths(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _secret_paths(value, f"{prefix}[{index}]")


def preflight(profile: str, certify_profile: Path | None = None,
              env: dict | None = None) -> dict:
    env = env or load()
    wanted = env["profiles"][profile]
    checks = [{"check": "platform", "status": "ok" if sys.platform.startswith(env["platform"])
               else "missing", "want": env["platform"], "found": sys.platform}]
    checks += [check_tool(name, env["tools"][name]) for name in wanted["tools"]]
    docker_ok = any(c["check"] == "tool:docker" and c["status"] == "ok" for c in checks)
    checks += [check_image(name, env["images"][name], docker_ok) for name in wanted["images"]]
    # a leftover container holds a namespace even when its port is free, whether or not
    # the profile itself needs docker (containers() is None when docker cannot answer)
    names = containers() if wanted["namespaces"] else None
    spaces = [check_namespace(name, env["namespaces"][name], names)
              for name in wanted["namespaces"]]
    checks += spaces
    if spaces:
        checks.append(ephemeral_overlap([p for row in spaces for p in row["ports"]]))
    if certify_profile is not None:
        checks.append(validate_certify_profile(certify_profile, env["certify_profile_required"]))
    if any(c["status"] == "invalid" for c in checks):
        verdict = INVALID
    elif all(c["status"] in ("ok", "risk") for c in checks):
        verdict = PASS
    else:
        verdict = BLOCKED
    return {"schema": "infrx.e2c.preflight/1", "profile": profile, "verdict": verdict,
            "exit": EXIT[verdict], "checks": checks,
            "not_ok": [c["check"] for c in checks if c["status"] not in ("ok", "risk")],
            "risks": [c["check"] for c in checks if c["status"] == "risk"]}


def main(argv: list[str] | None = None) -> int:
    env = load()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profile", choices=sorted(env["profiles"]))
    parser.add_argument("--certify-profile", type=Path,
                        help="validate this certify/load profile's shape (never runs it)")
    parser.add_argument("--out", type=Path, help="also write the JSON verdict here")
    args = parser.parse_args(argv)
    result = preflight(args.profile, args.certify_profile, env)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return result["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
