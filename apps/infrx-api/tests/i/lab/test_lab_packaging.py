#!/usr/bin/env python3
"""I2L packaging: the Lab's own origin, auth allowlist, control-service unit and edge site
(`LAB-ACCESS`, `LAB-PUBLISH`, `OPS-RECOVER` in research/plan/04-verification.md).

The files that ship are read as they stand (`deploy/lab/app/*`, `infra/lab/app/lab.json`,
the runbook). The two docker cases run the pinned Caddy (lib.sh CADDY_IMAGE) on the edge
files - `caddy validate` with no network, and one live edge on e3l's ports 57095/57096 in
front of a loopback stub - and skip visibly without docker. Nothing touches systemd, a
host path, AWS or anything hosted. The App-serving drill is E3L's l11
(tests/integration/lab_operate): App inference with the Lab down, broken and rolled back.
"""
from __future__ import annotations

import http.server
import json
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
DEPLOY, LAB_DEPLOY = API / "deploy", API / "deploy" / "lab" / "app"
LAB_INFRA = REPO / "infra" / "lab" / "app"
MANIFEST = LAB_INFRA / "lab.json"
RUNBOOK = LAB_INFRA / "README.md"
CONTROL_UNIT, SITE = "infrx-lab-control.service", "lab-control.caddy"
CADDY_IMAGE = re.search(r"^CADDY_IMAGE=(\S+)", (DEPLOY / "lib.sh").read_text(), re.M).group(1)
# The App edge's one-line import of the Lab site (WR-I2L-1); the composed file is what the box
# would run once the coordinator applies it.
IMPORT_LINE = "import /etc/caddy/lab/*.caddy"
EDGE_PORT, UPSTREAM_PORT = 57095, 57096   # e3l block: E2 layout +1500 leaves 57095-57099 free


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def unit(name: str) -> dict[str, list[str]]:
    """`Key=Value` lines of a unit file, continuations joined, repeated keys kept."""
    text = re.sub(r"\\\n\s*", " ", (LAB_DEPLOY / name).read_text())
    keys: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            key, _, value = line.partition("=")
            keys.setdefault(key.strip(), []).append(value.strip())
    return keys


def docker_run(name: str) -> list[str]:
    argv = shlex.split(unit(name)["ExecStart"][0])
    assert argv[:2] == ["/usr/bin/docker", "run"], argv[:2]
    return argv[2:]


def flag(argv: list[str], name: str) -> list[str]:
    return [argv[i + 1] for i, token in enumerate(argv) if token == name]


def site() -> str:
    return (LAB_DEPLOY / SITE).read_text()


def no_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


# --- the control-service unit ----------------------------------------------------------------
def test_i2l__the_control_service_starts_only_once_the_operator_enables_the_lab():
    """Disabled by default (hosted Lab needs P-08): with no enable marker the unit is a
    condition-failed no-op, even if something starts it. Oracle: a unit that runs without the
    marker would put a Lab process on the pilot box before anyone decided to."""
    marker = manifest()["enable_marker"]
    assert unit(CONTROL_UNIT).get("ConditionPathExists") == [marker]
    assert marker.startswith("/etc/infrx-lab/") and marker in RUNBOOK.read_text()


def test_i2l__no_app_runtime_unit_depends_on_a_lab_unit_or_the_reverse():
    """A Lab outage or a bad Lab deploy must not stop, restart or hold back App inference: no
    ordering or lifecycle link in either direction. Oracle: `PartOf=`/`Requires=`/`BindsTo=`
    between them would let `systemctl stop` of one take the other down."""
    links = ("Requires", "Requisite", "BindsTo", "PartOf", "Wants", "After", "Before",
             "Upholds", "PropagatesStopTo", "StopPropagatedFrom")
    app_units = sorted(p.name for p in DEPLOY.glob("*.service"))
    assert app_units, "the App runtime units moved"
    for name in app_units:
        assert "lab" not in no_comments((DEPLOY / name).read_text()).lower(), name
    lab = unit(CONTROL_UNIT)
    named = " ".join(v for key in links for v in lab.get(key, ()))
    for name in app_units:
        assert name.removesuffix(".service") not in named, (name, named)
    assert "docker.service" in named, "the Lab unit orders itself after docker only"


def test_i2l__the_control_service_is_loopback_least_privilege_bounded_and_its_own():
    """Protected: loopback only (the Lab site is its one public path), its own uid outside the
    runtime's group (no media root, no usage log), read-only, no capabilities, bounded so it
    cannot starve the gateway or worker, and its own env file, container and image variable,
    so a Lab release rolls forward and back without touching INFRX_IMAGE."""
    argv = docker_run(CONTROL_UNIT)
    run = " ".join(argv)
    command = argv[argv.index("${INFRX_LAB_IMAGE}") + 1:]
    assert command[:2] == ["uvicorn", "--factory"] and command[2] == manifest()["control"]["factory"]
    assert flag(command, "--host") == ["127.0.0.1"]
    assert flag(command, "--port") == [str(manifest()["control"]["port"])]
    assert flag(argv, "--network") == ["host"] and flag(argv, "--name") == ["infrx-lab-control"]
    uid, gid = flag(argv, "--user")[0].split(":")
    assert uid == gid and uid not in ("10000", "10001", "10002") and gid != "10000", (uid, gid)
    assert "--read-only" in argv and flag(argv, "--cap-drop") == ["ALL"]
    assert flag(argv, "--security-opt") == ["no-new-privileges"]
    assert flag(argv, "--memory") and flag(argv, "--cpus") and flag(argv, "--pids-limit")
    assert not flag(argv, "-v") and not flag(argv, "--volume") and "-p" not in argv
    assert "${INFRX_IMAGE}" not in run and "marlin2b-gateway.env" not in run
    env_file = manifest()["control"]["env_file"]
    assert unit(CONTROL_UNIT)["EnvironmentFile"] == [env_file] and flag(argv, "--env-file") == [env_file]


def test_i2l__the_control_service_drains_accepted_operations_before_it_is_killed():
    """A Lab rollback is a stop and a start: uvicorn finishes in-flight control operations
    within its grace, docker waits longer than that, systemd longer than docker (the gateway
    unit's rule). Oracle: a stop budget shorter than the grace kills an accepted operation."""
    argv = docker_run(CONTROL_UNIT)
    grace = int(flag(argv, "--timeout-graceful-shutdown")[0])
    stop = shlex.split(unit(CONTROL_UNIT)["ExecStop"][0])
    assert stop[:3] == ["/usr/bin/docker", "stop", "-t"] and stop[4:] == ["infrx-lab-control"]
    assert grace < int(stop[3]) < int(unit(CONTROL_UNIT)["TimeoutStopSec"][0])
    assert unit(CONTROL_UNIT)["Restart"] == ["always"]


# --- secrets, origins, the auth allowlist -----------------------------------------------------
def test_i2l__secret_names_only_and_every_name_the_files_read_is_declared():
    """Names only: no value in the manifest, and every variable the unit, the site or the Lab's
    .env.example reads is declared with its exposure. The Lab never holds a service-role key
    (R156: the session door decides). Oracle: an undeclared name is a setting nobody reviews;
    a value in the repository is a leaked secret."""
    env = manifest()["env"]
    declared = {item["name"]: item for items in env.values() for item in items}
    for item in declared.values():
        assert set(item) == {"name", "exposure", "purpose"}, item
        assert item["exposure"] in ("public", "server", "secret", "unit"), item
    read = set(re.findall(r"\$\{([A-Z_]+)\}", (LAB_DEPLOY / CONTROL_UNIT).read_text()))
    read |= set(re.findall(r"\{\$([A-Z_]+)[:}]", site()))
    read |= set(re.findall(r"^([A-Z_]+)=", (REPO / "apps/lab/.env.example").read_text(), re.M))
    assert read and read <= set(declared), sorted(read - set(declared))
    assert "SUPABASE_SERVICE_ROLE_KEY" not in {i["name"] for i in env["lab-web"]}
    runbook = RUNBOOK.read_text()
    assert all(name in runbook for name in declared), [n for n in declared if n not in runbook]


def test_i2l__the_lab_has_its_own_origin_and_its_callbacks_are_the_only_allowlist_change():
    """Separate origin and auth allowlist: the Lab's callbacks sit under the Lab's own origins,
    the App's Site URL is untouched, and the Lab's production entry waits on P-08 (the App's
    production allowlist says "only" today, so adding it is an App-side change). Oracle: a Lab
    callback on the App origin, or a Site URL moved to the Lab, breaks App sign-up."""
    m = manifest()
    origins = m["origins"]
    assert origins["lab"] != origins["app"] and origins["control"] not in (origins["app"], origins["lab"])
    assert m["auth"]["site_url"] == "unchanged"
    for scope, entries in m["auth"]["redirect_allowlist"].items():
        assert entries, scope
        for entry in entries:
            assert entry.endswith("/auth/callback**"), entry
            assert not entry.startswith(origins["app"]), entry
    assert m["auth"]["redirect_allowlist"]["production"] == [f"{origins['lab']}/auth/callback**"]
    assert m["enabled"] is False


# --- the edge site ------------------------------------------------------------------------------
def test_i2l__the_lab_site_is_the_control_origin_and_nothing_else():
    """One site, the control origin: `/lab/v1/*` to the loopback control port, everything else
    404, the consumer-key refusal ahead of the proxy, and never the App's address."""
    text = no_comments(site())
    assert re.search(r"^\{\$INFRX_LAB_CONTROL_SITE:([^}]+)\}", text, re.M).group(1) == \
        manifest()["origins"]["control"].removeprefix("https://")
    assert f"127.0.0.1:{manifest()['control']['port']}" in text
    assert text.index("@consumer_key") < text.index("reverse_proxy")
    assert "marlin2b.callbill.ai" not in text and "8001" not in text


def caddy(tmp_path: Path, files: dict[str, str], *, network: str = "none",
          env: dict[str, str] | None = None, detach: bool = False, name: str = ""):
    """The pinned Caddy over a composed /etc/caddy (main Caddyfile + lab/*.caddy)."""
    if shutil.which("docker") is None:
        pytest.skip("NOT RUN (needs docker): no docker binary")
    if subprocess.run(["docker", "image", "inspect", CADDY_IMAGE], capture_output=True).returncode:
        pytest.skip(f"NOT RUN (needs the pinned Caddy image present): {CADDY_IMAGE}")
    root = tmp_path / "caddy"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    envs = [a for k, v in (env or {}).items() for a in ("-e", f"{k}={v}")]
    head = ["docker", "run", "--rm", "--network", network, *envs, "-v", f"{root}:/etc/caddy:ro"]
    if detach:
        return subprocess.run([*head[:2], "-d", "--name", name, *head[2:], CADDY_IMAGE],
                              capture_output=True, text=True)
    return subprocess.run([*head, CADDY_IMAGE, "caddy", "validate", "--config",
                           "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                          capture_output=True, text=True)


def composed_main() -> str:
    """The App edge as WR-I2L-1 leaves it (the line appended until it is applied)."""
    main = (DEPLOY / "Caddyfile").read_text()
    return main if IMPORT_LINE in main else main + f"\n{IMPORT_LINE}\n"


def test_i2l__a_broken_or_hijacking_lab_site_never_validates_so_the_app_edge_is_unchanged(tmp_path):
    """The App edge imports the Lab site (WR-I2L-1) and lib.sh never installs a config the
    pinned Caddy refuses. So: no Lab site = the App's config as today; the Lab site composes;
    a broken Lab site, or one claiming the App's address, does not validate - the Lab install
    step refuses it and the running App edge keeps serving. Oracle: a Lab file that could
    replace or break the App site would make a bad Lab deploy an App outage."""
    ok = site()
    assert caddy(tmp_path / "a", {"Caddyfile": composed_main()}).returncode == 0
    good = caddy(tmp_path / "b", {"Caddyfile": composed_main(), "lab/lab.caddy": ok})
    assert good.returncode == 0, good.stderr[-600:]
    broken = caddy(tmp_path / "c", {"Caddyfile": composed_main(),
                                    "lab/lab.caddy": ok.replace("reverse_proxy", "reverse_proxi")})
    assert broken.returncode != 0
    hijack = caddy(tmp_path / "d", {"Caddyfile": composed_main(), "lab/lab.caddy": ok},
                   env={"INFRX_LAB_CONTROL_SITE": "marlin2b.callbill.ai"})
    assert hijack.returncode != 0 and "ambiguous" in (hijack.stderr + hijack.stdout).lower()


class _Upstream(http.server.BaseHTTPRequestHandler):
    def _answer(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"path": self.path, "auth": self.headers.get(
            "Authorization", "")}).encode())

    do_GET = do_POST = _answer

    def log_message(self, *args):
        pass


def _get(url: str, token: str | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode()


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_i2l__the_live_lab_edge_refuses_consumer_keys_and_is_unavailable_not_open_when_down(tmp_path):
    """The edge site run for real (pinned Caddy, host network, e3l ports) in front of a stub
    control service: a Lab session token reaches `/lab/v1/*`; a consumer `/v1` key is 401 on
    every path and never reaches it; other paths 404; with the control service down the Lab
    answers 503, never an App route. Oracle: a consumer key reaching a control operation."""
    if not (_free(EDGE_PORT) and _free(UPSTREAM_PORT)):
        pytest.skip(f"NOT RUN: e3l ports {EDGE_PORT}/{UPSTREAM_PORT} are held")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", UPSTREAM_PORT), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    name = f"infrx-e3l-lab-edge-{int(time.time())}"
    started = caddy(tmp_path, {"Caddyfile": "{\n\tadmin off\n}\n" + IMPORT_LINE + "\n",
                               "lab/lab.caddy": site()}, network="host", detach=True, name=name,
                    env={"INFRX_LAB_CONTROL_SITE": f"http://127.0.0.1:{EDGE_PORT}",
                         "INFRX_LAB_CONTROL_UPSTREAM": f"127.0.0.1:{UPSTREAM_PORT}"})
    try:
        assert started.returncode == 0, started.stderr[-400:]
        base = f"http://127.0.0.1:{EDGE_PORT}"
        for _ in range(100):
            if not _free(EDGE_PORT):
                break
            time.sleep(0.1)
        status, body = _get(f"{base}/lab/v1/deployments", "eyJ.lab-session.jwt")
        assert status == 200 and json.loads(body)["path"] == "/lab/v1/deployments"
        for path in ("/lab/v1/deployments", "/v1/chat/completions", "/"):
            status, body = _get(base + path, "sk-infrx-abcdefgh0123456789")
            assert status == 401 and "sk-infrx" not in body, (path, status, body)
        assert _get(f"{base}/v1/chat/completions", "eyJ.lab-session.jwt")[0] == 404
        server.shutdown()
        server.server_close()
        status, body = _get(f"{base}/lab/v1/deployments", "eyJ.lab-session.jwt")
        assert status == 503 and json.loads(body)["error"]["code"] == "lab_unavailable"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        server.server_close()


# --- the runbook ------------------------------------------------------------------------------
def runbook_commands(section: str) -> list[str]:
    """The shell lines of one `## <section>` of the runbook's fenced blocks."""
    text = RUNBOOK.read_text()
    body = text.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    return [line.strip() for block in re.findall(r"```(?:bash)?\n(.*?)```", body, re.S)
            for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_i2l__lab_rollback_restarts_only_the_lab_on_its_previous_release():
    """Rollback preserves accepted jobs: it stops and starts the Lab control unit on the
    previous INFRX_LAB_IMAGE (a draining stop, above) and removes or restores only the Lab
    site, never an App runtime unit, the engine, the App env file or the App site. Oracle: a
    rollback that restarts the gateway or worker would drop accepted App requests."""
    lines = runbook_commands("Rollback")
    assert any("systemctl restart infrx-lab-control" in line for line in lines), lines
    assert any("INFRX_LAB_IMAGE" in line for line in lines), lines
    joined = "\n".join(lines)
    for app in ("marlin2b-gateway", "infrx-worker", "marlin2b-vllm", "infrx-valkey",
                "marlin2b-gateway.env", "rollback.sh", "drain.sh"):
        assert app not in joined, app
