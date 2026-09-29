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


# WR-I2L-1's lib.sh half: edge_install validates each App site with the installed Lab sites
# mounted where the running edge sees them (it mounts $CADDY_DIR whole), so the import resolves
# during validation exactly as it will after the reload or the new container.
LIB_ANCHOR = ('local src=$1 site=(${INFRX_SITE:+-e "INFRX_SITE=$INFRX_SITE"}) f\n',
              '"${site[@]}" -v "$src/$f:/etc/caddy/Caddyfile:ro"')
LIB_WIRED = ('local src=$1 site=(${INFRX_SITE:+-e "INFRX_SITE=$INFRX_SITE"}) f lab=()\n'
             '  [ -d "$CADDY_DIR/lab" ] && lab=(-v "$CADDY_DIR/lab:/etc/caddy/lab:ro")\n',
             '"${site[@]}" "${lab[@]}" -v "$src/$f:/etc/caddy/Caddyfile:ro"')


def lib_as_wr_leaves_it() -> str:
    """deploy/lib.sh as WR-I2L-1 leaves it (the change applied until the coordinator lands it)."""
    lib = (DEPLOY / "lib.sh").read_text()
    if LIB_WIRED[1] in lib:
        return lib
    for old, new in zip(LIB_ANCHOR, LIB_WIRED):
        assert lib.count(old) == 1, f"lib.sh moved; re-anchor WR-I2L-1: {old!r}"
        lib = lib.replace(old, new)
    return lib


DOCKER_SHIM = """#!/bin/bash
# `docker run --rm` (the validation) is the real docker; the edge's other calls are recorded.
echo "docker $*" >> "$SHIM_LOG"
case "$1" in
  run) [ "$2" = --rm ] && exec "$REAL_DOCKER" "$@" ;;
  inspect) echo "$SHIM_RUNNING_IMAGE" ;;
esac
exit 0
"""


def edge_install(tmp_path: Path, app_site: str, running_image: str):
    """lib.sh edge_install of an App release on a box whose edge already has the Lab site
    installed (runbook section 5), with the pinned Caddy validating for real."""
    real = shutil.which("docker")
    caddy(tmp_path / "probe", {"Caddyfile": composed_main()})   # skips without docker/image
    root, src, shim = tmp_path / "root", tmp_path / "release", tmp_path / "bin"
    for d in (root / "etc/caddy/lab", root / "etc/caddy/infrx", src, shim):
        d.mkdir(parents=True)
    (root / "etc/caddy/lab" / SITE).write_text(site())
    for rel in ("Caddyfile", "infrx/Caddyfile", "infrx/Caddyfile.maintenance"):
        (root / "etc/caddy" / rel).write_text("# the running release\n")
    (src / "Caddyfile").write_text(app_site)
    (src / "Caddyfile.maintenance").write_text((DEPLOY / "Caddyfile.maintenance").read_text())
    (shim / "docker").write_text(DOCKER_SHIM)
    (shim / "docker").chmod(0o755)
    lib = tmp_path / "lib.sh"
    lib.write_text(lib_as_wr_leaves_it())
    log = tmp_path / "docker.log"
    done = subprocess.run(
        ["bash", "-c", 'set -euo pipefail; . "$0"; edge_install "$1"', str(lib), str(src)],
        capture_output=True, text=True, env={
            "PATH": f"{shim}:/usr/bin:/bin", "INFRX_ROOT": str(root), "REAL_DOCKER": real,
            "SHIM_LOG": str(log), "SHIM_RUNNING_IMAGE": running_image})
    calls = log.read_text().splitlines() if log.exists() else []
    return done, calls, root


def test_i2l__an_app_release_that_conflicts_with_the_installed_lab_site_never_replaces_the_edge(tmp_path):
    """Once the Lab site is installed, an App release (or a Caddy bump) is validated against it
    before the edge is touched: a release claiming the Lab site's address is exit 4 with no
    reload, no `docker rm -f caddy`, no new container and the running edge files unchanged, on a
    reload (same image) and on a replacement (new image) alike; a release that composes with it
    is installed. Oracle: lib.sh validating the App file alone would let an installed Lab file
    fail the reload or keep a new edge container from starting, leaving App inference unrouted."""
    address = re.search(r"^\{\$INFRX_LAB_CONTROL_SITE:([^}]+)\}", site(), re.M).group(1)
    conflicting = composed_main() + f"\n{address} {{\n\trespond 200\n}}\n"
    for n, running in enumerate((CADDY_IMAGE, "caddy:2.10")):
        done, calls, root = edge_install(tmp_path / f"bad{n}", conflicting, running)
        assert done.returncode == 4 and "does not validate" in done.stderr, (done.stderr, calls)
        assert [c for c in calls if " validate " in c] and all(
            "/etc/caddy/lab:ro" in c for c in calls if " validate " in c), calls
        assert not [c for c in calls if c.startswith(("docker rm", "docker exec", "docker run -d"))]
        assert (root / "etc/caddy/Caddyfile").read_text() == "# the running release\n"
        assert (root / "etc/caddy/infrx/Caddyfile").read_text() == "# the running release\n"
    done, calls, root = edge_install(tmp_path / "good", composed_main(), CADDY_IMAGE)
    assert done.returncode == 0, (done.stderr, calls)
    assert any(c.startswith("docker exec caddy caddy reload") for c in calls), calls
    assert (root / "etc/caddy/Caddyfile").read_text() == composed_main()


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


def test_i2l__the_control_factory_is_the_gateways_one_lab_operations_composition(monkeypatch):
    """WR-LAB-API-2c: the I2L control service composes `/lab/v1/control` through
    `gateway.pilot.lab_operations` on its own login - the gateway's one composition - so its
    listings read `NoControlReads` (a typed 503 until WR-LSQ-9) instead of passing the control
    store as `ControlReads` (an AttributeError logged as a bug). Nothing is dialled."""
    import asyncio

    from infrx.contracts import errors
    from infrx.gateway import pilot
    from infrx.lab.control import app as control_app
    from infrx.lab.control.operations import Operations
    for name in control_app.REQUIRED:
        monkeypatch.setenv(name, {control_app.DATABASE_URL: "postgresql://lab@127.0.0.1:1/lab",
                                  control_app.SUPABASE_URL: "http://127.0.0.1:1"}.get(name, "anon"))
    for name in ("CLICKHOUSE_URL", "S3_TRACE_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    _, control, traces = control_app._compose(control_app._settings(), store=None)
    ops = control.operations
    assert type(ops) is Operations and type(ops.reads) is pilot.NoControlReads and traces is None
    assert ops.control.access is control.access
    assert ops.control.store._connect is control.access.store._connect   # the Lab's own login
    try:
        asyncio.run(ops.reads.provider_servings("p"))
        refused = None
    except Exception as died:              # noqa: BLE001 - the type is compared
        refused = died
    assert type(refused) is errors.DependencyUnavailable, refused


def test_i2l__a_lab_worker_that_refuses_to_start_is_not_restarted_in_a_loop():
    """0-F5: `python -m infrx.lab.workers <role>` exits 2 on a refusal (a missing setting, or
    a role with no work source yet - R198) and 1 when a pass dies. Every Lab worker unit
    restarts on failure but never on exit 2 (`docker run --init` hands the container's status
    back): a refusal is final until the operator fixes the env file, not a restart every
    10 s. The unit set is every unit that runs the entry point, so a new one is held too."""
    units = {path: path.read_text() for path in sorted((DEPLOY / "lab").glob("*/*.service"))
             if "python -m infrx.lab.workers" in path.read_text()}
    assert {path.stem for path in units} == {
        f"infrx-lab-{role}" for role in ("eval", "checkpoints", "datasets", "judge",
                                         "annotation", "training", "rollout")}
    for path, text in units.items():
        service = text.split("[Service]", 1)[1].split("\n[", 1)[0]
        lines = [line.strip() for line in service.splitlines()]
        assert "Restart=on-failure" in lines, path.name
        assert "RestartPreventExitStatus=2" in lines, path.name
