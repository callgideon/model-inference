#!/usr/bin/env python3
"""I2B.b: `install.sh` (deploy), `drain.sh`, `rollback.sh` - `DEPLOY-FAILCLOSED`,
`BACKEND-DEPLOY` and `OPS-RECOVER` (research/plan/04-verification.md).

The scripts run for real, in bash, against a sandbox root (`INFRX_ROOT`) with stub
`systemctl`, `docker`, `curl`, `git`, `chown` and `aws` first on PATH. Every stub appends
to one `events.log`, so a case asserts the *order* of what a script did across all of
them - "the edge changed only after readiness" is a statement about two binaries. The
preflight is the real one (the docker stub runs its probe in this interpreter, as the
image would), except in the pilot cases, which need a pilot the repository cannot pass
today (G2/W3 pending) and therefore swap in a preflight that installs a pilot file.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tarfile

from . import support
from .support import preflight

DEPLOY = support.API_DIR / "deploy"
SHA = "c" * 40
IMAGE = "sha256:" + "b" * 64
ENV = "etc/marlin2b-gateway.env"
MONOLITH_ENV = "MODEL_ID=nemostation/marlin-2b\nSUPABASE_URL=https://x.supabase.co\n"
MONOLITH_UNIT = "[Service]\nExecStart=/opt/pytorch/bin/uvicorn gateway:app\n"

STUB = '''#!{python}
"""Stub `{name}`: logs its argv to events.log and answers from behaviour.json."""
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "events.log").open("a") as log:
    log.write("{name} " + " ".join(args) + "\\n")
spec = json.loads((here / "behaviour.json").read_text())
name = "{name}"
if name == "git":
    if args[-2:] == ["rev-parse", "HEAD"]:
        print(spec["head"])
    elif args[-2:] == ["status", "--porcelain"]:
        print(spec["dirty"], end="")
elif name == "curl":
    if args[-1] in spec["curl_fails"]:
        raise SystemExit(22)
    # the monolith's /health asks the engine: down until the engine has been restarted
    if (spec["health_needs_engine"] and args[-1] == "http://127.0.0.1:8001/health"
            and "systemctl restart marlin2b-vllm\\n" not in (here / "events.log").read_text()):
        raise SystemExit(22)
elif name == "systemctl":
    raise SystemExit(spec["systemctl"].get(args[0], 0))
elif name == "docker":
    if args[0] == "image":
        print(spec["image"])
    elif args[0] == "exec" and spec["reload_fails"]:
        raise SystemExit(1)
    elif args[0] == "run" and spec["validate_fails"] and any(
            a.endswith("/" + spec["validate_fails"] + ":/etc/caddy/Caddyfile:ro") for a in args):
        raise SystemExit(1)
    elif args[0] == "inspect":
        if spec["caddy_image"] is None:
            raise SystemExit(1)
        print(spec["caddy_image"])
    elif args[0] == "run" and {image_preflight!r} in args:
        rest = args[args.index({image_preflight!r}) + 1:]
        os.execv({python!r}, [{python!r}, {preflight!r}, *rest])
'''

# A preflight that installs a pilot file without reading anything: the repository's own
# pilot is refused today by design (G2 composition, W3 entry points and engine pin).
PILOT_PREFLIGHT = '''#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
env = pathlib.Path(args[args.index("--env-file") + 1])
image = args[args.index("--image") + 1]
with open(pathlib.Path(__file__).parent / "events.log", "a") as log:
    log.write("preflight " + " ".join(args) + "\\n")
env.parent.mkdir(parents=True, exist_ok=True)
env.write_text(f"INFRX_MODE=pilot\\nINFRX_IMAGE={image}\\n")
'''


class Host:
    """A sandbox root, the stubs, and helpers to run a script against them."""

    def __init__(self, tmp_path, monkeypatch, parameters=None):
        self.root = tmp_path / "root"
        (self.root / "etc").mkdir(parents=True)
        self.bin = tmp_path / "stub-bin"
        self.bin.mkdir()
        support.stubs(tmp_path, monkeypatch, parameters)          # `aws` (+ PATH entry)
        (tmp_path / "stub-bin" / "systemctl").unlink()
        (tmp_path / "stub-bin" / "docker").unlink()
        for name in ("systemctl", "docker", "curl", "git", "chown"):
            path = self.bin / name
            path.write_text(STUB.format(python=sys.executable, name=name,
                                        image_preflight=preflight.IMAGE_PREFLIGHT,
                                        preflight=str(DEPLOY / "preflight.py")))
            path.chmod(0o755)
        self.behave()

    def behave(self, **changes):
        spec = {"head": SHA, "dirty": "", "curl_fails": [], "systemctl": {},
                "image": IMAGE, "caddy_image": None, "reload_fails": False,
                "health_needs_engine": False, "validate_fails": None}
        spec.update(changes)
        (self.bin / "behaviour.json").write_text(json.dumps(spec))

    def run(self, script, *args, **env) -> subprocess.CompletedProcess:
        return self._bash([str(DEPLOY / script), *args], env)

    def shell(self, code, **env) -> subprocess.CompletedProcess:
        """Lines of a runbook step, run against the same sandbox and stubs."""
        return self._bash(["-c", code], env)

    def _bash(self, argv, env) -> subprocess.CompletedProcess:
        base = {"INFRX_ROOT": str(self.root), "ENV_OWNER": support.owner_name(),
                "PYTHON": sys.executable, "SERVE_SCRIPT": str(self.serve), "POLL_S": "0.05",
                "READY_S": "1", "ENGINE_READY_S": "1", "PATH": os.environ["PATH"]}
        return subprocess.run(["bash", *argv], capture_output=True, text=True,
                              env={**base, **env}, cwd=str(self.root))

    @property
    def serve(self) -> pathlib.Path:
        return support.serve_script(self.bin.parent, image=support.PINNED)

    @property
    def events(self) -> list[str]:
        log = self.bin / "events.log"
        return log.read_text().splitlines() if log.exists() else []

    def clear(self):
        (self.bin / "events.log").unlink(missing_ok=True)

    def of(self, name) -> list[str]:
        return [e for e in self.events if e.startswith(name + " ")]

    def file(self, relative) -> pathlib.Path:
        return self.root / relative

    def pilot_preflight(self) -> str:
        path = self.bin / "pilot-preflight.py"
        path.write_text(PILOT_PREFLIGHT)
        return str(path)

    def monolith(self):
        """The box before I2B: the monolith's env file and gateway unit, no mode."""
        self.file(ENV).write_text(MONOLITH_ENV)
        unit = self.file("etc/systemd/system/marlin2b-gateway.service")
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(MONOLITH_UNIT)


def backups(host) -> list[pathlib.Path]:
    return sorted((host.root / "var/backups/infrx").glob("*"))


# --- install.sh: refusals ------------------------------------------------------------
def test_deploy_failclosed__a_refused_install_changes_nothing_on_the_host(tmp_path,
                                                                          monkeypatch):
    """DEPLOY-FAILCLOSED end to end through the deploy script: a denied secret read, and
    the repository's own pilot today (refused by the composition gate and W3's pending
    pins), both exit 2 with the env file and every unit byte-identical, no unit touched
    by systemctl, no state directory made, the edge untouched - and no backup of its own
    left behind (it holds a copy of the previous env file's secrets), while an earlier
    run's backup is kept."""
    for params in ({**{n: {"value": v} for n, v in support.VALID.items()},
                    "/model-inference/pg_journal_url": {"error": "AccessDeniedException"}},
                   None):
        host = Host(tmp_path / str(params is None), monkeypatch, params)
        host.monolith()
        earlier = host.file("var/backups/infrx/20260101T000000Z-" + "e" * 40)
        earlier.mkdir(parents=True)                     # a previous run's: must survive
        before = {p: host.file(p).read_bytes()
                  for p in (ENV, "etc/systemd/system/marlin2b-gateway.service")}
        done = host.run("install.sh", INFRX_MODE="pilot")
        assert done.returncode == 2, done.stderr
        assert {p: host.file(p).read_bytes() for p in before} == before
        assert sorted(p.name for p in host.file("etc/systemd/system").iterdir()) == [
            "marlin2b-gateway.service"]
        assert host.of("systemctl") == [] and host.of("chown") == []
        assert not host.file("var/lib/infrx").exists()
        assert [e for e in host.of("docker") if "caddy" in e] == []
        assert "refusing to install" in done.stderr
        assert backups(host) == [earlier]


def test_deploy_failclosed__only_a_committed_checkout_is_deployed(tmp_path, monkeypatch):
    """The image is built from exactly a commit: a dirty tree, or HEAD other than the
    RELEASE the runbook names, stops before the build and before any host change. An
    unusable mode stops before even that - no git, no build, no backup."""
    host = Host(tmp_path, monkeypatch)
    for mode in ("", "prod"):
        done = host.run("install.sh", INFRX_MODE=mode)
        assert done.returncode == 2 and "INFRX_MODE must be" in done.stderr, mode
        assert host.events == [] and backups(host) == [], mode
    host.behave(dirty=" M apps/infrx-api/infrx/config.py\n")
    done = host.run("install.sh", INFRX_MODE="dev")
    assert done.returncode == 2 and "uncommitted" in done.stderr
    host.behave()
    done = host.run("install.sh", INFRX_MODE="dev", RELEASE="d" * 40)
    assert done.returncode == 2 and "RELEASE" in done.stderr
    assert host.of("docker") == [] and host.of("systemctl") == [] and backups(host) == []
    # root deploys ubuntu's checkout: every git call names it a safe directory
    assert host.of("git") and all(e.startswith("git -c safe.directory=") for e in host.of("git"))


# --- install.sh: success -------------------------------------------------------------
def test_backend_deploy__a_dev_install_pins_the_image_it_probed(tmp_path, monkeypatch):
    """BACKEND-DEPLOY, fresh target: the image built from the commit is the one the probe
    ran in and the one written for the units; the units are the repository's bytes; the
    engine is started (never restarted) and healthy before the gateway restarts; dev
    installs no edge; the backup holds the previous files and lists the new ones."""
    host = Host(tmp_path, monkeypatch)
    host.monolith()
    done = host.run("install.sh", INFRX_MODE="dev")
    assert done.returncode == 0, done.stderr
    assert preflight.read_env(host.file(ENV)).get("INFRX_IMAGE") == IMAGE
    probes = [e for e in host.of("docker") if preflight.IMAGE_PREFLIGHT in e]
    assert len(probes) == 1 and f" {IMAGE} " in probes[0]
    for name in ("marlin2b-gateway.service", "infrx-worker.service", "marlin2b-vllm.service"):
        assert host.file(f"etc/systemd/system/{name}").read_bytes() == (DEPLOY / name).read_bytes()
    assert host.of("systemctl") == [
        "systemctl daemon-reload", "systemctl enable marlin2b-vllm marlin2b-gateway",
        "systemctl start marlin2b-vllm", "systemctl restart marlin2b-gateway"]
    order = [e for e in host.events if e.startswith(("systemctl restart", "curl"))]
    assert order[0].endswith("http://127.0.0.1:8000/health"), order
    assert order[1] == "systemctl restart marlin2b-gateway"
    assert order[-1].endswith("http://127.0.0.1:8001/health"), order
    assert [e for e in host.of("docker") if "caddy" in e] == []
    host.clear()
    assert host.run("install.sh", INFRX_MODE="dev", ENGINE="restart").returncode == 0
    assert "systemctl restart marlin2b-vllm" in host.of("systemctl")
    backup = backups(host)[0]
    with tarfile.open(backup / "files.tar") as tar:
        assert ENV in tar.getnames()
        assert tar.extractfile(ENV).read().decode() == MONOLITH_ENV
    assert "etc/systemd/system/infrx-worker.service" in (backup / "absent").read_text()


def test_backend_deploy__a_pilot_install_opens_the_edge_only_after_readiness(
        tmp_path, monkeypatch):
    """Pilot order (infra/README.md §7): the index and the engine, then the runtime, then
    the gateway's and the worker's /readyz, and only then the edge - both sites, normal
    and maintenance, validated with the pinned Caddy before either is installed: a site
    that does not validate is exit 4 with no edge file written and nothing reloaded."""
    host = Host(tmp_path, monkeypatch)
    done = host.run("install.sh", INFRX_MODE="pilot", PREFLIGHT=host.pilot_preflight())
    assert done.returncode == 0, done.stderr
    assert host.of("systemctl") == [
        "systemctl daemon-reload",
        "systemctl enable marlin2b-vllm infrx-valkey infrx-worker marlin2b-gateway",
        "systemctl start infrx-valkey", "systemctl start marlin2b-vllm",
        "systemctl restart infrx-worker marlin2b-gateway"]
    events = host.events
    ready = [i for i, e in enumerate(events)
             if e.endswith(("http://127.0.0.1:8001/readyz", "http://127.0.0.1:8002/readyz"))]
    assert len(ready) == 2, events
    ready = max(ready)
    edge = [i for i, e in enumerate(events) if e.startswith("docker") and "caddy" in e]
    assert edge and min(edge) > ready
    validates = [e for e in host.of("docker") if "caddy validate" in e]
    mounted = sorted(re.search(r"-v \S+/(Caddyfile\S*):/etc/caddy/Caddyfile:ro", e).group(1)
                     for e in validates)
    assert mounted == ["Caddyfile", "Caddyfile.maintenance"], validates
    assert all("--network none" in e and "caddy@sha256:" in e for e in validates)
    served = [e for e in host.of("docker") if e.startswith("docker run -d --name caddy")]
    assert len(served) == 1 and "caddy@sha256:" in served[0] and "--network host" in served[0]
    assert host.file("etc/caddy/Caddyfile").read_bytes() == (DEPLOY / "Caddyfile").read_bytes()
    assert host.file("etc/caddy/infrx/Caddyfile.maintenance").read_bytes() == (
        DEPLOY / "Caddyfile.maintenance").read_bytes()

    bad = Host(tmp_path / "bad", monkeypatch)
    bad.behave(validate_fails="Caddyfile.maintenance")
    done = bad.run("install.sh", INFRX_MODE="pilot", PREFLIGHT=bad.pilot_preflight())
    assert done.returncode == 4 and "Caddyfile.maintenance does not validate" in done.stderr
    assert not bad.file("etc/caddy").exists()
    assert not [e for e in bad.of("docker") if "caddy reload" in e or "run -d" in e]


def test_ops_recover__a_runtime_that_is_not_ready_leaves_the_edge_alone(tmp_path,
                                                                        monkeypatch):
    """A runtime that never answers /readyz is exit 4 naming the backup to roll back
    to; the edge is not touched (it keeps serving maintenance or the previous site). No
    automatic rollback: the file is validated. The worker's readiness counts as much as
    the gateway's."""
    host = Host(tmp_path, monkeypatch)
    host.behave(curl_fails=["http://127.0.0.1:8001/readyz"])
    done = host.run("install.sh", INFRX_MODE="pilot", PREFLIGHT=host.pilot_preflight())
    assert done.returncode == 4
    [backup] = backups(host)
    assert f"rollback.sh {backup}" in done.stderr
    assert [e for e in host.of("docker") if "caddy" in e] == []
    host.clear()
    host.behave(curl_fails=["http://127.0.0.1:8002/readyz"])
    assert host.run("install.sh", INFRX_MODE="pilot", PREFLIGHT=host.pilot_preflight()).returncode == 4
    assert [e for e in host.of("docker") if "caddy" in e] == []


# --- drain.sh -------------------------------------------------------------------------
def _pilot_host(tmp_path, monkeypatch) -> Host:
    host = Host(tmp_path, monkeypatch)
    assert host.run("install.sh", INFRX_MODE="pilot",
                    PREFLIGHT=host.pilot_preflight()).returncode == 0
    host.behave(caddy_image="running")
    host.clear()
    return host


def test_ops_recover__drain_closes_the_edge_before_stopping_the_worker(tmp_path,
                                                                       monkeypatch):
    """pause: the active site becomes maintenance (so a Caddy restart keeps it) and is
    reloaded **before** the runtime stops - a reload that fails stops nothing and says the
    edge is still open (exit 4); resume opens the edge only after /readyz, and a runtime
    that is not ready keeps maintenance (exit 4)."""
    host = _pilot_host(tmp_path, monkeypatch)
    active = host.file("etc/caddy/Caddyfile")
    host.behave(caddy_image="running", reload_fails=True)
    done = host.run("drain.sh", "pause")
    assert done.returncode == 4 and "still OPEN" in done.stderr
    assert host.of("systemctl") == []
    host.behave(caddy_image="running")
    host.clear()
    assert host.run("drain.sh", "pause").returncode == 0
    assert active.read_bytes() == (DEPLOY / "Caddyfile.maintenance").read_bytes()
    assert host.events == [
        "docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile "
        "--address unix//config/admin.sock",
        "systemctl stop infrx-worker marlin2b-gateway"]

    host.clear()
    host.behave(caddy_image="running", curl_fails=["http://127.0.0.1:8001/readyz"])
    assert host.run("drain.sh", "resume").returncode == 4
    assert active.read_bytes() == (DEPLOY / "Caddyfile.maintenance").read_bytes()
    assert host.of("docker") == []

    host.clear()
    host.behave(caddy_image="running")
    assert host.run("drain.sh", "resume").returncode == 0
    assert active.read_bytes() == (DEPLOY / "Caddyfile").read_bytes()
    events = host.events
    assert events[0] == "systemctl start infrx-worker marlin2b-gateway"
    assert events[-1].startswith("docker exec caddy caddy reload")
    assert events[-2].endswith("http://127.0.0.1:8002/readyz"), events


# --- rollback.sh -------------------------------------------------------------------------
def test_ops_recover__rollback_restores_every_replaced_file(tmp_path, monkeypatch):
    """After a dev install over the monolith, rollback.sh puts the previous env file and
    gateway unit back byte for byte, removes (and disables) the units that did not exist,
    and restarts the gateway onto them - and only the gateway: without ENGINE=restart the
    engine is left alone. A dev -> legacy revert is allowed: dev was never metered. The backup holds the previous env file's secrets: root-only, 0700 and 0600."""
    host = Host(tmp_path, monkeypatch)
    host.monolith()
    assert host.run("install.sh", INFRX_MODE="dev").returncode == 0
    [backup] = backups(host)
    for path, mode in ((backup.parent, 0o700), (backup, 0o700), (backup / "files.tar", 0o600)):
        assert stat.S_IMODE(path.stat().st_mode) == mode, (path.name, oct(path.stat().st_mode))
    host.clear()
    done = host.run("rollback.sh", str(backup))
    assert done.returncode == 0, done.stderr
    assert host.file(ENV).read_text() == MONOLITH_ENV
    assert host.file("etc/systemd/system/marlin2b-gateway.service").read_text() == MONOLITH_UNIT
    assert not host.file("etc/systemd/system/infrx-worker.service").exists()
    assert "systemctl disable --now infrx-worker.service" in host.events
    assert "systemctl restart marlin2b-gateway" in host.events
    assert host.events[-1].endswith("http://127.0.0.1:8001/health")
    # a plain rollback leaves the engine alone: only ENGINE=restart (runbook R2) restarts it
    assert not [e for e in host.events if e.startswith("systemctl restart marlin2b-vllm")]


def test_ops_recover__an_install_never_writes_into_an_existing_backup(tmp_path, monkeypatch):
    """Two installs stamped with the same time must not share a backup directory: the
    second would overwrite the first's copy of the replaced files, and a refused one would
    delete it. An existing directory stops the install (exit 2), the earlier backup intact."""
    host = Host(tmp_path, monkeypatch)
    (host.bin / "date").write_text("#!/usr/bin/env bash\necho 20260923T000000.000000000Z\n")
    (host.bin / "date").chmod(0o755)
    host.monolith()
    assert host.run("install.sh", INFRX_MODE="dev").returncode == 0
    [backup] = backups(host)
    first = (backup / "files.tar").read_bytes()
    done = host.run("install.sh", INFRX_MODE="dev")
    assert done.returncode == 2 and "exists already" in done.stderr
    assert backups(host) == [backup] and (backup / "files.tar").read_bytes() == first


def test_deploy_failclosed__rollback_never_returns_a_pilot_to_an_unmetered_runtime(
        tmp_path, monkeypatch):
    """infra/README.md §8: a host that served pilot is not rolled back to a runtime that
    cannot settle metered work - refused (exit 2) before anything is stopped or written -
    unless the operator states, in exactly those words, that no pilot request was ever
    accepted (a failed first cutover), in which case the monolith's files come back."""
    host = Host(tmp_path, monkeypatch)
    host.monolith()
    assert host.run("install.sh", INFRX_MODE="pilot",
                    PREFLIGHT=host.pilot_preflight()).returncode == 0
    [backup] = backups(host)
    host.clear()
    current = host.file(ENV).read_bytes()
    done = host.run("rollback.sh", str(backup))
    assert done.returncode == 2 and "drain.sh pause" in done.stderr
    assert host.file(ENV).read_bytes() == current and host.events == []
    # the statement is exact: a typo, a yes, a padded copy are not it
    for typo in ("no-pilot-request-accepted", "yes", " no-pilot-request-was-accepted"):
        done = host.run("rollback.sh", str(backup), ROLLBACK_TO_UNMETERED=typo)
        assert done.returncode == 2, typo
        assert host.file(ENV).read_bytes() == current and host.events == [], typo
    done = host.run("rollback.sh", str(backup),
                    ROLLBACK_TO_UNMETERED="no-pilot-request-was-accepted")
    assert done.returncode == 0, done.stderr
    assert host.file(ENV).read_text() == MONOLITH_ENV


def _runbook() -> tuple[str, str]:
    """30-pause's edge lines and 90-revert's lines from rollback.sh on, as bash that runs
    against the Host sandbox with `d` = this checkout's deploy directory."""
    steps = support.REPO / "infra" / "rollout" / "steps"
    pause = (steps / "30-pause.sh").read_text()
    pause = pause[pause.index('( . "$d/lib.sh"'):pause.index('echo "paused')]
    revert = (steps / "90-revert.sh").read_text()
    revert = revert[revert.rindex("\n", 0, revert.index('"$d/rollback.sh"')) + 1:]
    return (f'set -euo pipefail; d="{DEPLOY}"\n{pause}',
            f'set -euo pipefail; d="{DEPLOY}"; previous=before\n{revert}')


R2 = {"ROLLBACK_TO_UNMETERED": "no-pilot-request-was-accepted"}


def test_ops_recover__the_r2_revert_reopens_the_edge_on_the_restored_runtime(tmp_path,
                                                                              monkeypatch):
    """Runbook R2, in runbook order, with the steps' own lines: 30-pause makes maintenance
    the active site, so step 8's backup holds it and rollback.sh restores it - the edge
    would stay 503 for good. 90-revert therefore ends with `drain.sh resume`, after the
    engine restart: the normal site comes back once the restored runtime is ready."""
    pause, revert = _runbook()
    host = Host(tmp_path, monkeypatch)
    host.monolith()
    active = host.file("etc/caddy/Caddyfile")
    done = host.shell(pause)                                                    # step 5
    assert done.returncode == 0, done.stderr
    assert active.read_bytes() == (DEPLOY / "Caddyfile.maintenance").read_bytes()
    host.behave(caddy_image="running", curl_fails=["http://127.0.0.1:8001/readyz"])
    assert host.run("install.sh", INFRX_MODE="pilot",                           # step 8: exit 4
                    PREFLIGHT=host.pilot_preflight()).returncode == 4
    [backup] = backups(host)
    host.behave(caddy_image="running")
    host.clear()
    done = host.shell(revert, BACKUP=str(backup), **R2)
    assert done.returncode == 0, done.stderr
    assert host.file(ENV).read_text() == MONOLITH_ENV
    assert active.read_bytes() == (DEPLOY / "Caddyfile").read_bytes()
    events = host.events
    engine = events.index("systemctl restart marlin2b-vllm")
    assert events[-1].startswith("docker exec caddy caddy reload"), events
    assert events[-2].endswith("http://127.0.0.1:8001/health") and len(events) - 2 > engine


def test_ops_recover__r2_restores_the_engine_before_the_gateway_that_asks_it(tmp_path,
                                                                             monkeypatch):
    """Step 8 can fail on the engine itself ("the engine is not healthy"), and the restored
    monolith's /health asks the engine: a gateway restarted first never becomes ready. So
    R2 has rollback.sh restart the engine onto its restored unit and wait for it, then the
    gateway, then the edge. An engine that does not come up stops the revert there - exit
    4, the gateway untouched, the edge in maintenance, and the message says what next; a
    gateway that does not come up after the engine did stops it the same way."""
    pause, revert = _runbook()
    host = Host(tmp_path, monkeypatch)
    host.monolith()
    active = host.file("etc/caddy/Caddyfile")
    assert host.shell(pause).returncode == 0
    host.behave(caddy_image="running", curl_fails=["http://127.0.0.1:8000/health"])
    done = host.run("install.sh", INFRX_MODE="pilot", PREFLIGHT=host.pilot_preflight())
    assert done.returncode == 4 and "engine is not healthy" in done.stderr
    [backup] = backups(host)

    host.behave(caddy_image="running", health_needs_engine=True, systemctl={"restart": 1})
    host.clear()
    done = host.shell(revert, BACKUP=str(backup), **R2)
    assert done.returncode == 4 and "fix the engine, then drain.sh resume" in done.stderr
    assert active.read_bytes() == (DEPLOY / "Caddyfile.maintenance").read_bytes()
    assert not [e for e in host.events
                if e.startswith("systemctl restart marlin2b-gateway") or "caddy reload" in e]

    # the engine comes up but the restored gateway does not: exit 4, the edge untouched
    host.behave(caddy_image="running", curl_fails=["http://127.0.0.1:8001/health"])
    host.clear()
    done = host.shell(revert, BACKUP=str(backup), **R2)
    assert done.returncode == 4 and "the restored runtime is not ready" in done.stderr
    assert active.read_bytes() == (DEPLOY / "Caddyfile.maintenance").read_bytes()
    assert not [e for e in host.events if "caddy reload" in e]

    host.behave(caddy_image="running", health_needs_engine=True)
    host.clear()
    done = host.shell(revert, BACKUP=str(backup), **R2)
    assert done.returncode == 0, done.stderr
    assert active.read_bytes() == (DEPLOY / "Caddyfile").read_bytes()
    events = host.events
    assert (events.index("systemctl restart marlin2b-vllm")
            < events.index("curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8000/health")
            < events.index("systemctl restart marlin2b-gateway")), events
