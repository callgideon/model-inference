"""I8 slice 2: the versioned env schema, checked before every runtime start.

Failure oracles: the runtime itself ignores a name it does not read (shown first - a
mistyped pool size silently keeps the default), so without `envcheck` a hand edit, a
retired name or an env file written for another image starts. Each case below is a file
the runtime would have started on; `envcheck` refuses it, names the setting and never
prints a value, and the unit's ExecStartPre turns that refusal into a failed start.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess

import pytest

from . import support
from .support import preflight

DEPLOY = support.API_DIR / "deploy"
SECRET = f"{support.MARKER}-db-password"
PILOT = {
    "INFRX_MODE": "pilot", "MODEL_ID": "nemostation/marlin-2b", "MAX_INFLIGHT": "16",
    "USAGE_LOG": "/var/lib/infrx/usage/usage.jsonl", "INFRX_IMAGE": support.IMAGE,
    "INFRX_RELEASE_SHA": "b" * 40, "UPSTREAM": "http://127.0.0.1:8000",
    "VALKEY_URL": "valkey://127.0.0.1:6379/0", "PROCESSING_CACHE_DIR": "/opt/dlami/nvme/processing",
    "SUPABASE_URL": "https://fcbnscgsymzdykendbrc.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": f"{support.MARKER}-service-role-key",
    "DATABASE_URL": f"postgresql://infrx:{SECRET}@db.invalid:5432/infrx",
    "S3_MEDIA_BUCKET": "bucket", "MAX_VIDEO_SECONDS": "82", "DATABASE_POOL_MAX_SIZE": "6",
}


@pytest.fixture(autouse=True)
def _image_interpreter(monkeypatch):
    """The box runs this in the runtime image (3.12.14); this host's venv may be older."""
    monkeypatch.setattr(preflight, "REQUIRED_PYTHON", (3, 0, 0))


def check(text: str, mode: str = "pilot") -> dict:
    verdict = preflight.envcheck(text, mode)
    assert SECRET not in json.dumps(verdict) and support.MARKER not in json.dumps(verdict)
    return verdict


def test_ops_continuous__the_runtime_ignores_a_mistyped_name_which_envcheck_refuses():
    from infrx.config import from_env
    intended = {k: v for k, v in PILOT.items() if k != "DATABASE_POOL_MAX_SIZE"}
    env = {**intended, "DATABASE_POOL_MAX": "4"}                  # the typo
    assert from_env(env).deployment.database_pool_max_size == 10     # silently the default
    good = preflight.render(intended)
    assert check(good)["ok"], check(good)["problems"]
    verdict = check(good + "DATABASE_POOL_MAX=4\n")
    assert not verdict["ok"]
    assert any(p.startswith("DATABASE_POOL_MAX: not in the env schema") for p in verdict["problems"])


@pytest.mark.parametrize("edit, named", [
    (lambda t: t.replace("DATABASE_POOL_MAX_SIZE=6\n", "DATABASE_POOL_MAX_SIZE=6\nDATABASE_POOL_MAX_SIZE=40\n"),
     "DATABASE_POOL_MAX_SIZE: set twice"),
    (lambda t: re.sub(r"DATABASE_URL=.*\n", "", t), "DATABASE_URL: missing"),
    (lambda t: t + "GATEWAY_API_KEY=" + support.MARKER + "-legacy-key-000\n",
     "GATEWAY_API_KEY: forbidden in pilot mode"),
    (lambda t: t + "PRICE_TABLE_VERSION=v1\n", "PRICE_TABLE_VERSION"),
    (lambda t: t.split("\n", 1)[1], "no INFRX_ENV_SCHEMA header"),
    (lambda t: t.replace(preflight.schema_id(), "0:0000000000000000"), "written for env schema"),
    (lambda t: t.replace("INFRX_IMAGE=sha256:", "INFRX_IMAGE=latest"), "INFRX_IMAGE: the value is not"),
    (lambda t: t + "not a line\n", "not NAME=VALUE"),
    (lambda t: t.replace("MAX_VIDEO_SECONDS=82", "MAX_VIDEO_SECONDS=eighty"), "does not start"),
])
def test_deploy_failclosed__envcheck_refuses_a_file_the_runtime_would_start_on(edit, named):
    verdict = check(edit(preflight.render(PILOT)))
    assert not verdict["ok"] and any(named in p for p in verdict["problems"]), verdict["problems"]


def test_deploy_failclosed__the_schema_id_moves_with_the_names_it_covers(monkeypatch):
    before = preflight.schema_id()
    monkeypatch.setattr(preflight, "TUNABLE", preflight.TUNABLE + ("A_NEW_KNOB",))
    assert preflight.schema_id() != before and preflight.schema_id().startswith("1:")


def _pre_line(name: str) -> str:
    (line,) = [v for v in re.sub(r"\\\n\s*", " ", (DEPLOY / name).read_text()).splitlines()
               if v.startswith("ExecStartPre=/bin/sh")]
    return line.partition("=")[2]


@pytest.mark.parametrize("name", ["marlin2b-gateway.service", "infrx-worker.service"])
def test_deploy_failclosed__each_runtime_unit_refuses_to_start_on_a_refused_env_file(
        tmp_path, monkeypatch, name):
    """The unit's own ExecStartPre, as systemd runs it (`$$` -> `$`, the EnvironmentFile's
    names in the environment), against the docker stub that runs this preflight: a good
    file starts, a refused one fails the start - and the check comes before ExecStart."""
    text = (DEPLOY / name).read_text()
    assert text.index("ExecStartPre=/bin/sh") < text.index("\nExecStart=")
    argv = shlex.split(_pre_line(name))
    assert argv[:2] == ["/bin/sh", "-c"]
    command = argv[2].replace("$$", "$")
    assert "--network none" in command and "--env-file /dev/stdin" in command
    env_file = tmp_path / "marlin2b-gateway.env"
    command = command.replace("/etc/marlin2b-gateway.env", str(env_file))
    support.stubs(tmp_path, monkeypatch)
    dev = {k: v for k, v in PILOT.items() if k not in ("SUPABASE_SERVICE_ROLE_KEY",)}
    dev["INFRX_MODE"] = "dev"
    for body, code in ((preflight.render(dev), 0), (preflight.render(dev) + "TYPO_NAME=1\n", 2)):
        env_file.write_text(body)
        done = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True,
                              env={**os.environ, "INFRX_IMAGE": support.IMAGE, "INFRX_MODE": "dev"})
        assert done.returncode == code, (done.stdout, done.stderr)
        assert SECRET not in done.stdout + done.stderr
    assert "TYPO_NAME: not in the env schema" in done.stdout
    logged = (tmp_path / "stub-bin" / "argv.log").read_text()
    assert SECRET not in logged and support.IMAGE in logged
