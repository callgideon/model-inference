#!/usr/bin/env python3
"""I2B.a packaging: the pinned runtime image, the units that run it, the edge, the config
schema and the pilot's resource and startup rules (`BACKEND-DEPLOY`, `DEPLOY-FAILCLOSED`,
`OPS-RECOVER` in research/plan/04-verification.md).

Everything here reads the files that ship (`deploy/*.service`, `deploy/Caddyfile*`,
`deploy/Dockerfile`) or drives `preflight.py` through the same stubs as the I0 suite. No
case starts a container, touches systemd or reaches a network: the local rehearsal
(`deploy/rehearse.sh`) is where these files are run for real.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess

import pytest

from . import support
from .support import preflight

DEPLOY = support.API_DIR / "deploy"
RUNTIME_UNITS = ("marlin2b-gateway.service", "infrx-worker.service")
CONTAINER_UNITS = RUNTIME_UNITS + ("infrx-valkey.service",)
LONG_RUNNING = CONTAINER_UNITS
ENV_FILE = "/etc/marlin2b-gateway.env"


def unit(name: str) -> dict[str, list[str]]:
    """`Key=Value` lines of a unit file, continuations joined, repeated keys kept."""
    text = re.sub(r"\\\n\s*", " ", (DEPLOY / name).read_text())
    keys: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            key, _, value = line.partition("=")
            keys.setdefault(key.strip(), []).append(value.strip())
    return keys


def docker_run(name: str) -> list[str]:
    """The `docker run` argv of a container unit's ExecStart."""
    argv = shlex.split(unit(name)["ExecStart"][0])
    assert argv[:2] == ["/usr/bin/docker", "run"], (name, argv[:2])
    return argv[2:]


def flag(argv: list[str], name: str) -> list[str]:
    return [argv[i + 1] for i, token in enumerate(argv) if token == name]


def stop_seconds(name: str) -> int:
    stop = shlex.split(unit(name)["ExecStop"][0])
    assert stop[:3] == ["/usr/bin/docker", "stop", "-t"] and len(stop) == 5, (name, stop)
    assert stop[4:] == flag(docker_run(name), "--name"), f"{name} stops another container"
    return int(stop[3])


# --- the image -----------------------------------------------------------------------
def test_backend_deploy__every_image_is_pinned_by_digest():
    """A tag moves; a digest does not. The runtime's two base images, the index and the
    edge are pinned by registry digest, and the runtime units run `${INFRX_IMAGE}`, which
    the manifest only accepts as a content-addressed image id."""
    froms = re.findall(r"^FROM (\S+)", (DEPLOY / "Dockerfile").read_text(), re.M)
    assert len(froms) == 2, froms
    for image in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), image
    index = [t for t in docker_run("infrx-valkey.service") if t.startswith("valkey/valkey")]
    assert len(index) == 1 and re.search(r"@sha256:[0-9a-f]{64}$", index[0]), index
    assert re.search(r"CADDY_IMAGE=caddy@sha256:[0-9a-f]{64}", (DEPLOY / "lib.sh").read_text())
    for name in RUNTIME_UNITS:
        assert "${INFRX_IMAGE}" in docker_run(name), name
    image_key = next(key for key in preflight.MANIFEST if key.env == "INFRX_IMAGE")
    assert preflight.SHAPES[image_key.shape]("sha256:" + "0" * 64)
    assert not preflight.SHAPES[image_key.shape]("infrx-runtime:latest")


def test_backend_deploy__the_gateway_runs_the_factory_from_what_the_image_copies():
    """The cutover (G2 request 1, R48): the gateway unit runs `uvicorn --factory
    infrx.gateway.app:create_app`, a callable of the code the image copies; the retired
    `gateway.py` shim is gone, and every path the Dockerfile copies or compiles exists and
    is let into the build context by `Dockerfile.dockerignore`."""
    import importlib

    argv = docker_run("marlin2b-gateway.service")
    command = argv[argv.index("${INFRX_IMAGE}") + 1:]
    assert command[:3] == ["uvicorn", "--factory", "infrx.gateway.app:create_app"], command
    module, _, name = command[2].partition(":")
    assert callable(getattr(importlib.import_module(module), name))
    assert not (support.API_DIR / "gateway.py").exists()
    docker = (DEPLOY / "Dockerfile").read_text()
    copied = [source for line in re.findall(r"^COPY (?!--)(.+)$", docker, re.M)
              for source in line.split()[:-1]]
    compiled = re.search(r"^RUN python -m compileall -q (.+)$", docker, re.M).group(1).split()
    allowed = {line[1:].rstrip("/") for line
               in (DEPLOY / "Dockerfile.dockerignore").read_text().splitlines()
               if line.startswith("!")}
    for source in copied:
        assert (support.API_DIR / source).exists() and source in allowed, source
    for path in compiled:
        assert any(path == source or source.startswith(path + "/") for source in copied), path


def test_backend_deploy__the_image_runs_nothing_as_root():
    """The Dockerfile's last word is an unprivileged user, and the pilot probe refuses an
    image whose interpreter runs as uid 0 - checked where it runs, not where it is built."""
    text = (DEPLOY / "Dockerfile").read_text()
    user = re.findall(r"^USER (\S+)", text, re.M)
    assert user and user[-1].split(":")[0] not in ("root", "0"), user


# --- the units -------------------------------------------------------------------------
def test_backend_deploy__the_units_are_valid_systemd():
    """`systemd-analyze verify` over every shipped unit. The only tolerated complaint is
    the engine script's absolute path, which exists on the box and not on this host."""
    if shutil.which("systemd-analyze") is None:
        pytest.fail("systemd-analyze is not installed: the units are unverified")
    names = sorted(p.name for p in DEPLOY.glob("*.service"))
    done = subprocess.run(["systemd-analyze", "verify", "--man=no",
                           *[str(DEPLOY / n) for n in names]],
                          capture_output=True, text=True)
    complaints = [line for line in (done.stderr + done.stdout).splitlines()
                  if line.strip() and "models/marlin2b/serve.sh is not executable" not in line]
    assert complaints == [], complaints


def test_ops_recover__every_container_unit_stops_later_than_docker_does():
    """systemd killing a `docker run` client does not stop the container: the unit's
    `TimeoutStopSec` must outlast `docker stop -t`, which must name the unit's own
    container. The engine's 10 s docker default was the box's defect (I1B)."""
    for name in LONG_RUNNING + ("marlin2b-vllm.service",):
        if name == "marlin2b-vllm.service":
            stop = shlex.split(unit(name)["ExecStop"][0])
            assert stop[:3] == ["/usr/bin/docker", "stop", "-t"] and len(stop) == 5, stop
            seconds = int(stop[3])
        else:
            seconds = stop_seconds(name)
        assert seconds >= 10, name
        assert int(unit(name)["TimeoutStopSec"][0]) > seconds, name


def test_ops_recover__the_worker_drain_outlasts_one_generation():
    """W3's drain lets in-flight attempts finish before it fences the rest: the worker's
    stop budget covers one whole generation (08 §5 `GENERATION_TIMEOUT_S`)."""
    from infrx.contracts.limits import DEFAULTS

    assert stop_seconds("infrx-worker.service") > DEFAULTS.generation_timeout_s
    assert stop_seconds("marlin2b-gateway.service") >= 120


def test_ops_recover__the_worker_drains_before_the_engine_stops():
    """W3's worker unit: it runs `python -m infrx.worker` (the composition root around
    WorkerService, whose in-process reaper is W2 request 7's `recover()` caller - so there
    is no timer to run it twice), it is `PartOf=` the engine and ordered after it, so an
    engine stop or restart drains it first, and tini (`--init`) delivers the SIGTERM that
    starts the drain. The gateway gets tini too: uvicorn's graceful stop needs the signal."""
    worker = unit("infrx-worker.service")
    assert worker.get("PartOf") == ["marlin2b-vllm.service"]
    assert "marlin2b-vllm.service" in worker["After"][0].split()
    assert docker_run("infrx-worker.service")[-3:] == ["python", "-m", "infrx.worker"]
    assert preflight.WORKER_ENTRIES == ("infrx.worker.__main__",)
    for name in RUNTIME_UNITS:
        assert "--init" in docker_run(name), name
    assert not list(DEPLOY.glob("*.timer")), "a second reaper path (W3 limit: one path)"


def test_backend_deploy__runtime_containers_run_unprivileged_and_bounded():
    """Least privilege per process: own uid (gateway != worker), read-only root, no
    capabilities, no privilege gain, and a memory/pid bound. Only the gateway may write
    the media root; the worker reads it."""
    uids = {}
    for name in CONTAINER_UNITS:
        argv = docker_run(name)
        user = flag(argv, "--user")
        assert user and user[0].split(":")[0] not in ("0", "root"), name
        uids[name] = user[0].split(":")[0]
        for required in ("--read-only",):
            assert required in argv, (name, required)
        assert flag(argv, "--cap-drop") == ["ALL"], name
        assert "no-new-privileges" in flag(argv, "--security-opt"), name
        assert flag(argv, "--memory") and flag(argv, "--pids-limit"), name
    assert uids["marlin2b-gateway.service"] != uids["infrx-worker.service"]
    media = "${PROCESSING_CACHE_DIR}"
    assert f"{media}:{media}" in flag(docker_run("marlin2b-gateway.service"), "-v")
    assert f"{media}:{media}:ro" in flag(docker_run("infrx-worker.service"), "-v")


def test_backend_deploy__the_runtime_listens_only_on_loopback():
    """The engine and the index are never public, and neither is the gateway: Caddy is
    the only listener on a public address. The installer refuses a non-loopback engine
    or index address, and a serve.sh whose default bind is not loopback."""
    gateway = docker_run("marlin2b-gateway.service")
    assert gateway[gateway.index("--host") + 1] == "127.0.0.1"
    valkey = docker_run("infrx-valkey.service")
    assert valkey[valkey.index("--bind") + 1] == "127.0.0.1"
    assert valkey[valkey.index("--protected-mode") + 1] == "yes"
    loopback = preflight.SHAPES["loopback_url"]
    assert loopback("http://127.0.0.1:8000")
    for public in ("http://0.0.0.0:8000", "http://10.0.0.5:8000", "https://127.0.0.1:8000",
                   "http://127.0.0.1.evil.example:8000"):
        assert not loopback(public), public
    index = preflight.SHAPES["loopback_valkey_url"]
    assert index("valkey://127.0.0.1:6379/0")
    assert not index("valkey://10.0.0.5:6379/0")


def test_deploy_failclosed__a_public_engine_bind_is_refused(tmp_path):
    """serve.sh publishes the engine port on `${BIND:-...}`; a pilot install refuses a
    default other than loopback, because that is an OpenAI endpoint with no key."""
    script = support.serve_script(tmp_path, image=support.PINNED)
    default = script.read_text()
    for public in ("${BIND:-0.0.0.0}", "0.0.0.0", "${BIND}"):
        script.write_text(default.replace("${BIND:-127.0.0.1}", public))
        problems = preflight.engine_problems(script, "pilot")
        assert len(problems) == 1 and "127.0.0.1" in problems[0], (public, problems)
    script.write_text(default.replace("${BIND:-127.0.0.1}", "127.0.0.1"))   # W3's form
    assert preflight.engine_problems(script, "pilot") == []


def test_backend_deploy__the_engine_takes_its_settings_from_the_validated_file():
    """W3's one source per setting: serve.sh reads `ENGINE_MAX_NUM_SEQS` and
    `PROCESSING_CACHE_DIR` from the environment and refuses a second value on its command
    line, so the engine unit passes no argument and reads the file preflight validated.
    The media root lives on the instance-store NVMe a stop wipes: the engine and the
    gateway (its writer) recreate it, owned by the gateway's uid, on every start."""
    engine = unit("marlin2b-vllm.service")
    assert engine.get("EnvironmentFile") == [ENV_FILE]
    assert engine["ExecStart"] == ["/home/ubuntu/model-inference/models/marlin2b/serve.sh"]
    create = "+/usr/bin/install -d -o 10001 -g 10000 -m 2750 ${PROCESSING_CACHE_DIR}"
    for name in ("marlin2b-vllm.service", "marlin2b-gateway.service"):
        assert create in unit(name)["ExecStartPre"], name
    assert "ENGINE_MAX_NUM_SEQS" in preflight.TUNABLE
    assert any(key.env == "PROCESSING_CACHE_DIR" for key in preflight.MANIFEST)


def test_backend_deploy__one_env_file_configures_every_runtime_unit():
    """One configuration authority: every unit that runs the runtime image reads the file
    `preflight.py apply` validated - systemd for `${INFRX_IMAGE}`, docker for the rest -
    so no process can start on configuration the probe never saw."""
    assert (DEPLOY / "lib.sh").read_text().count(f"ENV_FILE=${{ENV_FILE:-{ENV_FILE}}}") == 1
    for name in RUNTIME_UNITS:
        assert unit(name)["EnvironmentFile"] == [ENV_FILE], name
        assert flag(docker_run(name), "--env-file") == [ENV_FILE], name


# --- the config schema -----------------------------------------------------------------
class _Recording(dict):
    """A mapping that remembers every name it is asked about."""

    def __init__(self):
        super().__init__()
        self.names: set[str] = set()

    def get(self, key, default=None):
        self.names.add(key)
        return super().get(key, default)

    def __contains__(self, key):
        self.names.add(key)
        return super().__contains__(key)

    def __getitem__(self, key):
        self.names.add(key)
        return super().__getitem__(key)


def test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads():
    """08 §5/§5.1 + PilotSettings + DeploymentSettings + the F1 names, names only: the
    manifest, the tunables and the never-written names partition exactly the names
    `config.from_env` reads. A name the runtime starts reading without a schema entry
    fails here instead of being silently unconfigurable (or silently mistyped)."""
    from infrx.config import from_env

    recording = _Recording()
    from_env(recording)
    manifest = {key.env for key in preflight.MANIFEST} - {"INFRX_IMAGE"}   # systemd's
    tunable, never = set(preflight.TUNABLE), set(preflight.NOT_SETTABLE)
    assert len(tunable) == len(preflight.TUNABLE), "a tunable is listed twice"
    assert not (manifest & tunable) and not (manifest & never) and not (tunable & never)
    assert manifest | tunable | never == recording.names, (
        sorted(recording.names - (manifest | tunable | never)),
        sorted((manifest | tunable | never) - recording.names))


def test_deploy_failclosed__a_setting_outside_the_schema_installs_nothing(
        tmp_path, monkeypatch, capsys):
    """A mistyped name would be ignored by the runtime and believed by the operator; a
    manifest or never-written name would bypass SSM or an authorization; a value with a
    newline would write a second variable - a non-loopback UPSTREAM after the checked one,
    and the last line wins. Each is a refusal with the previous file byte-identical, no
    restart, and a message that names the setting but never echoes its value."""
    made = support.stubs(tmp_path, monkeypatch)
    for pair in ("MAX_ACTIVE_JOB=4", "CONSOLE_CURSOR_SECRET=" + "x" * 32,
                 "DATABASE_URL=postgresql://a@b/c", "JUDGE_MODE=live"):
        cfg = support.config(tmp_path, settings=(pair,))
        before = cfg.env_file.read_bytes()
        assert preflight.apply(cfg) == preflight.REFUSED, pair
        assert cfg.env_file.read_bytes() == before and made.systemctl_calls == []
        assert f"--set {pair.partition('=')[0]}" in capsys.readouterr().err
    cfg = support.config(tmp_path, settings=("MAX_ACTIVE_JOBS=4", "MAX_ACTIVE_JOBS=5"))
    assert preflight.apply(cfg) == preflight.REFUSED
    assert "given twice" in capsys.readouterr().err and made.systemctl_calls == []
    cfg = support.config(tmp_path, settings=("MAX_ACTIVE_JOBS=4\nUPSTREAM=http://10.0.0.5:8000",))
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    err = capsys.readouterr().err
    assert cfg.env_file.read_bytes() == before and made.systemctl_calls == []
    assert "MAX_ACTIVE_JOBS: the value contains a newline" in err and "10.0.0.5" not in err


def test_deploy_failclosed__a_tunable_is_written_and_typed_by_the_runtime(
        tmp_path, monkeypatch, capsys):
    """A schema name reaches the file; a value the runtime cannot read (`abc`, a negative
    cap) is refused by the probe's `validate_runtime(from_env(...))` over the staged
    bytes, before anything is replaced or restarted. The engine's sequence count and the
    worker's runner count must be positive integers in serve.sh's own grammar
    (`[1-9][0-9]*`): the runtime reads 0 and vLLM does not start on it, and serve.sh
    refuses '007' or a non-ASCII digit - each is refused here, by shape, first."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, settings=("MAX_ACTIVE_JOBS=4",))
    assert preflight.apply(cfg) == 0
    assert preflight.read_env(cfg.env_file).get("MAX_ACTIVE_JOBS") == "4"
    installed = cfg.env_file.read_bytes()
    for bad in ("MAX_ACTIVE_JOBS=abc", "MAX_ACTIVE_JOBS=-1", "DATABASE_POOL_MAX_SIZE=0"):
        (made.dir / "systemctl.log").unlink(missing_ok=True)
        cfg = support.config(tmp_path, previous=None, settings=(bad,))
        assert preflight.apply(cfg) == preflight.REFUSED, bad
        assert cfg.env_file.read_bytes() == installed and made.systemctl_calls == [], bad
        assert "does not start" in capsys.readouterr().err
    for bad in ("ENGINE_MAX_NUM_SEQS=0", "ENGINE_MAX_NUM_SEQS=-1", "ENGINE_MAX_NUM_SEQS=abc",
                "ENGINE_MAX_NUM_SEQS=007", "ENGINE_MAX_NUM_SEQS=\u0663",
                "ENGINE_MAX_NUM_SEQS=\u00b2", "WORKER_CONCURRENCY=0"):
        (made.dir / "systemctl.log").unlink(missing_ok=True)
        cfg = support.config(tmp_path, previous=None, settings=(bad,))
        assert preflight.apply(cfg) == preflight.REFUSED, bad
        assert cfg.env_file.read_bytes() == installed and made.systemctl_calls == [], bad
        assert "is not a valid positive_int" in capsys.readouterr().err, bad
    cfg = support.config(tmp_path, previous=None, settings=("ENGINE_MAX_NUM_SEQS=32",))
    assert preflight.apply(cfg) == 0
    assert preflight.read_env(cfg.env_file).get("ENGINE_MAX_NUM_SEQS") == "32"


# --- the runtime image at install time ------------------------------------------------
def test_deploy_failclosed__pilot_runs_only_the_pinned_runtime_image(tmp_path, monkeypatch,
                                                                     capsys):
    """A pilot install without an image id, or with a tag, is refused before any read of
    a secret matters; with one, the probe runs **inside** that image, offline, reading
    the staged file on stdin, and the id is written into the same file the units read."""
    made = support.stubs(tmp_path, monkeypatch)
    for image in ("", "infrx-runtime:latest"):
        cfg = support.config(tmp_path, mode="pilot", image=image)
        before = cfg.env_file.read_bytes()
        assert preflight.apply(cfg) == preflight.REFUSED
        assert cfg.env_file.read_bytes() == before and made.systemctl_calls == []
        assert "INFRX_IMAGE" in capsys.readouterr().err
    cfg = support.config(tmp_path, image=support.IMAGE)
    assert preflight.apply(cfg) == 0
    assert preflight.read_env(cfg.env_file)["INFRX_IMAGE"] == support.IMAGE
    runs = [line for line in made.argv.splitlines() if line.startswith("docker ")]
    assert runs == [f"docker run --rm -i --network none {support.IMAGE} python "
                    f"{preflight.IMAGE_PREFLIGHT} probe --mode dev --env-file /dev/stdin"]


def test_deploy_failclosed__a_pilot_image_must_be_unprivileged_and_carry_the_worker(
        tmp_path, monkeypatch):
    """Inside the image, a pilot refuses a root interpreter and a runtime without W3's
    worker entry point - the unit would start something that cannot run. It is absent
    today: that refusal is the named pending item."""
    env = tmp_path / "pilot.env"
    env.write_text("INFRX_MODE=pilot\n")
    problems = preflight.probe(env, "pilot")["problems"]
    for entry in preflight.WORKER_ENTRIES:
        assert f"PENDING(W3): {entry}" in " ".join(problems), entry
    assert not [p for p in problems if "as root" in p]
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    monkeypatch.setattr(preflight, "_importable", lambda module: True)
    problems = preflight.probe(env, "pilot")["problems"]
    assert [p for p in problems if "as root" in p]
    assert not [p for p in problems if "PENDING(W3)" in p]


def test_deploy_failclosed__pilot_needs_w3s_recorded_engine_pin(tmp_path):
    """The named placeholder for W3's pin: without `serving-version.json` beside serve.sh
    a pilot install is refused, and with one it must record the digest serve.sh runs."""
    script = support.serve_script(tmp_path, image=support.PINNED, recorded=False)
    problems = preflight.engine_problems(script, "pilot")
    assert len(problems) == 1 and problems[0].startswith("PENDING(W3)"), problems
    pin = tmp_path / preflight.SERVING_VERSION
    pin.write_text(json.dumps({"image": "vllm/vllm-openai@sha256:" + "1" * 64}))
    problems = preflight.engine_problems(script, "pilot")
    assert len(problems) == 1 and "does not record" in problems[0], problems
    pin.write_text(json.dumps({"image": support.PINNED}))
    assert preflight.engine_problems(script, "pilot") == []
    assert preflight.engine_problems(script, "dev") == []


def test_deploy_failclosed__a_host_below_its_disk_budget_installs_no_pilot(
        tmp_path, monkeypatch, capsys):
    """A pilot install refuses a filesystem with less free space than its declared budget
    (checked at the nearest existing parent of a directory not yet created); the budget
    is the est. allocation of infra/README.md §2."""
    made = support.stubs(tmp_path, monkeypatch)
    cfg = support.config(tmp_path, mode="pilot", disk=((str(tmp_path / "not" / "yet"), 2**62),))
    before = cfg.env_file.read_bytes()
    assert preflight.apply(cfg) == preflight.REFUSED
    assert cfg.env_file.read_bytes() == before and made.systemctl_calls == []
    assert "GiB free, the pilot budget is" in capsys.readouterr().err
    assert preflight.disk_problems(((str(tmp_path), 1),)) == []
    assert preflight.DISK_BUDGET == (("/var/lib/infrx", 10 * 2**30),
                                     ("/opt/dlami/nvme/processing", 60 * 2**30))
    # the budgeted media directory is the root the installer writes as PROCESSING_CACHE_DIR
    assert preflight.Config(mode="pilot", env_file=tmp_path).media_root == preflight.DISK_BUDGET[1][0]


# --- the edge --------------------------------------------------------------------------
def _responses(text: str) -> list[tuple[str, int]]:
    return [(body, int(status)) for body, status in re.findall(r"respond `([^`]*)` (\d{3})", text)]


def _envelope(code: str, **infrx):
    from infrx.contracts import errors

    made = errors.DomainError(code=code, **infrx)
    return json.loads(errors.envelope(made, request_id="{http.request.uuid}").model_dump_json())


def test_backend_deploy__the_edge_hides_operator_paths_and_sanitizes_health():
    """Caddy is the public surface: `/metrics`, `/readyz` and `/internal` are 404 there;
    public `/health` is `{"ok": true|false}` whatever the gateway's body says; every
    proxied request goes to the gateway on loopback, never to the engine; bodies are
    bounded at 08 §5's `MAX_REQUEST_BYTES`; and the edge's own errors are the contract's
    envelopes. Its admin API - which can replace all of that - is a unix socket in a
    volume only Caddy mounts, never the loopback the gateway and worker share, and every
    reload the scripts do names that socket."""
    from infrx.contracts.limits import DEFAULTS

    for site in ("Caddyfile", "Caddyfile.maintenance"):
        admin = re.findall(r"^\s*admin (\S+)", (DEPLOY / site).read_text(), re.M)
        assert admin == ["unix//config/admin.sock"], (site, admin)
    reloads = [line for script in sorted(DEPLOY.glob("*.sh"))
               for line in script.read_text().splitlines() if "caddy reload" in line]
    assert reloads and all("--address unix//config/admin.sock" in r for r in reloads), reloads
    assert "-v caddy_config:/config" in (DEPLOY / "lib.sh").read_text()
    for name in CONTAINER_UNITS:
        assert not [v for v in flag(docker_run(name), "-v") if "caddy" in v], name
    text = (DEPLOY / "Caddyfile").read_text()
    private = re.search(r"@private path (.+)", text).group(1).split()
    for path in ("/metrics", "/metrics/*", "/readyz", "/readyz/*"):
        assert path in private, path
    assert set(re.findall(r"reverse_proxy (\S+)", text)) == {"127.0.0.1:8001"}
    size = re.search(r"max_size (\d+)MiB", text).group(1)
    assert int(size) * 2**20 == DEFAULTS.max_request_bytes
    declared = re.search(r"int\(\{http.request.header.Content-Length\}\) > (\d+)`", text)
    assert declared and int(declared.group(1)) == DEFAULTS.max_request_bytes
    assert re.search(r"handle @oversized \{\s*error 413\s*\}", text)
    bodies = _responses(text)
    assert ('{"ok":true}', 200) in bodies and ('{"ok":false}', 503) in bodies
    health = text[text.index("handle /health"):text.index("\thandle {")]
    assert {body for body, _ in _responses(health)} == {'{"ok":true}', '{"ok":false}'}
    assert "handle_errors 502 503 504 {" in text
    down = text[text.index("handle_errors 502 503 504"):text.index("# A declared length")]
    assert "@health path /health" in down and _responses(down) == [('{"ok":false}', 503)]
    envelopes = {status: json.loads(body) for body, status in bodies if body.startswith('{"error"')}
    assert envelopes == {404: _envelope("not_found"), 413: _envelope("request_too_large")}


#: G3 request (e) / G2 E3B2: what must cross the edge untouched, each way.
PASSTHROUGH = ("Location", "Retry-After", "Preference-Applied", "Idempotency-Replayed",
               "Inference-Id", "Last-Event-ID", "Prefer", "Idempotency-Key")


def _edge_block(text: str, path: str) -> str:
    """The handler a public request for `path` reaches at the edge: the private 404, the
    sanitised /health, or the catch-all proxy's own block (Caddy's `handle` blocks are
    exclusive; the matcherless one is last)."""
    import fnmatch

    private = re.search(r"@private path (.+)", text).group(1).split()
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in private):
        return "private"
    if path == "/health":
        return "health"
    start = text.index("\thandle {\n")
    depth, end = 0, start
    for end, char in enumerate(text[start:], start):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth == 0 and char == "}":
            break
    return text[start:end + 1]


def test_backend_deploy__the_edge_proxies_jobs_and_uploads_untouched_and_unbuffered():
    """G3 request (e) and G4U: every route the cutover mounts - chat, the five jobs routes,
    the three upload routes - reaches the gateway through the catch-all proxy, which streams
    (`flush_interval -1`, no buffering or encoding: `/v1/jobs/{h}/events` is SSE), outlasts a
    quiet stream's keepalive, admits an upload's bytes, and rewrites no header: the contract
    headers pass through both ways."""
    from infrx.contracts.limits import DEFAULTS
    from infrx.gateway.routes import ingress, uploads

    text = (DEPLOY / "Caddyfile").read_text()
    routes = [("POST", ingress.CHAT_PATH), *ingress.JOBS_ROUTES,
              ("POST", uploads.UPLOADS_PATH), ("PUT", uploads.DESTINATION_PATH),
              ("POST", uploads.COMPLETE_PATH)]
    reached = {path: _edge_block(text, path.replace("{handle}", ingress.SAMPLE_JOB_HANDLE))
               for _, path in routes}
    assert {path for path, where in reached.items() if where in ("private", "health")} == set()
    block = reached[ingress.CHAT_PATH]
    assert set(reached.values()) == {block}
    assert re.search(r"^\t\treverse_proxy 127\.0\.0\.1:8001 \{$", block, re.M), block
    assert re.search(r"^\s*flush_interval -1$", block, re.M), "a stream would be buffered"
    for directive in ("header_up", "header_down", "encode", "request_buffers",
                      "response_buffers", "buffer_requests", "buffer_responses"):
        assert not re.search(rf"^\s*{directive}\b", block, re.M), directive
    for name in PASSTHROUGH:                      # comments aside, nowhere in the site
        code = "\n".join(line.split("#")[0] for line in text.splitlines())
        assert name.lower() not in code.lower(), name
    read = int(re.search(r"read_timeout (\d+)s", block).group(1))
    assert read > DEFAULTS.sse_keepalive_s
    size = int(re.search(r"max_size (\d+)MiB", text).group(1)) * 2**20
    assert size >= DEFAULTS.max_media_bytes


def test_backend_deploy__maintenance_answers_every_request_with_the_retry_envelope():
    """drain.sh's edge: nothing is proxied, public health is down, and every request is
    the contract's `dependency_unavailable` 503 whose `retry_after_s` equals the
    `Retry-After` header."""
    text = (DEPLOY / "Caddyfile.maintenance").read_text()
    assert "reverse_proxy" not in text
    retry = int(re.search(r"header Retry-After (\d+)", text).group(1))
    bodies = _responses(text)
    assert ('{"ok":false}', 503) in bodies
    envelopes = [json.loads(body) for body, status in bodies if body.startswith('{"error"')]
    assert envelopes == [_envelope("dependency_unavailable", retry_after_s=retry)]
