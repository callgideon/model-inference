#!/usr/bin/env python3
"""Local stubs for the installer suite: a fake `aws`, a fake `systemctl`, fake
runtime interpreters and a generated `serve.sh`.

Nothing in `tests/i` touches AWS, systemd, the network or the pilot host. Every
system interaction of `deploy/preflight.py` goes through one of three seams, and all
three are stubbed here:

* `aws` and `systemctl` are resolved through `PATH`, so a stub directory prepended to
  `PATH` is the whole injection - the module needs no test hook;
* the runtime interpreter is `--runtime-python`, so a stub executable stands in for
  the interpreter the unit would use;
* the filesystem is the `--env-file` path, so a temporary directory stands in for
  `/etc`.

The stubs record their own `argv`, which is what makes "no secret reaches an
argument" an assertion rather than a claim.
"""
from __future__ import annotations

import getpass
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[2]
REPO = API_DIR.parents[1]


def _load():
    """`deploy/preflight.py` by path.

    Not `import deploy.preflight`: `deploy/` is a directory of scripts with no
    `__init__.py`, and pytest's `--import-mode=importlib` synthesises the `tests`
    package, so a path load is the only form that behaves the same under `pytest`,
    under the mutation runner's copied tree and when the file is run directly.
    """
    path = API_DIR / "deploy" / "preflight.py"
    spec = importlib.util.spec_from_file_location("infrx_preflight", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load()

# A value that must never appear in stdout, stderr, a log or an argument. Every stub
# secret below contains it, so one assertion covers every path a value can take.
MARKER = "MARKER-SECRET-do-not-log"

VALID = {
    "/model-inference/supabase_url": "https://fcbnscgsymzdykendbrc.supabase.co",
    "/model-inference/supabase_service_role_key": f"{MARKER}-service-role-key",
    "/model-inference/pg_journal_url": f"postgresql://infrx:{MARKER}@db.invalid:5432/infrx",
    "/model-inference/marlin2b_api_key": f"{MARKER}-legacy-gateway-key",
}

AWS_STUB = '''#!{python}
"""A fake `aws ssm get-parameter`. Answers from params.json; records every argv."""
import json, pathlib, sys

here = pathlib.Path(__file__).resolve().parent
with (here / "argv.log").open("a") as log:
    log.write("aws " + " ".join(sys.argv[1:]) + "\\n")
spec = json.loads((here / "params.json").read_text())
name = sys.argv[sys.argv.index("--name") + 1]
entry = spec.get(name)
if entry is None:
    sys.stderr.write("An error occurred (ParameterNotFound) when calling the "
                     "GetParameter operation: Parameter " + name + " not found.\\n")
    raise SystemExit(255)
if "error" in entry:
    sys.stderr.write("An error occurred (" + entry["error"] + ") when calling the "
                     "GetParameter operation: refused.\\n")
    raise SystemExit(255)
sys.stdout.write(entry["value"] + "\\n")
'''

SYSTEMCTL_STUB = '''#!{python}
"""A fake `systemctl`. Records the verb and units; exits with the code in codes.json."""
import json, pathlib, sys

here = pathlib.Path(__file__).resolve().parent
with (here / "systemctl.log").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
with (here / "argv.log").open("a") as log:
    log.write("systemctl " + " ".join(sys.argv[1:]) + "\\n")
codes = json.loads((here / "codes.json").read_text())
raise SystemExit(codes.get(sys.argv[1], 0))
'''

# Kills whatever ran it. Used as `--runtime-python`, it makes the installer die
# exactly between staging the file and renaming it, with no test hook in the module.
KILLER_STUB = '''#!{python}
import os, signal
os.kill(os.getppid(), signal.SIGKILL)
'''

BABBLER_STUB = '''#!{python}
import sys
sys.stdout.write("not json at all\\n")
'''

# Records the runtime probe's own argv and then becomes the real interpreter, so the
# marker audit covers the probe subprocess's arguments as well as `aws`'s.
LOGGING_PYTHON_STUB = '''#!{python}
import os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
with (here / "argv.log").open("a") as log:
    log.write("python " + " ".join(sys.argv[1:]) + "\\n")
os.execv({python!r}, [{python!r}, *sys.argv[1:]])
'''

# A digest-pinned engine image, i.e. what W3 owes the pilot. `models/marlin2b/serve.sh`
# defaults to the floating `:nightly` tag today, which is the recorded pending gap.
PINNED = "vllm/vllm-openai@sha256:" + "0" * 64

SERVE_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
IMAGE=${{IMAGE:-{image}}}
exec docker run --rm --name "marlin2b-$PORT" "$IMAGE" /model \\
  --served-model-name marlin2b \\
  --hf-overrides '{{"architectures":["Qwen3_5ForConditionalGeneration"]}}' \\
  --dtype bfloat16 {extra} "$@"
"""


class Stubs:
    """A stub directory on `PATH`, plus the recordings the cases assert on."""

    def __init__(self, directory: pathlib.Path):
        self.dir = directory

    def _write(self, name: str, body: str) -> None:
        path = self.dir / name
        path.write_text(body.format(python=sys.executable))
        path.chmod(0o755)

    def logging_python(self) -> str:
        """An interpreter wrapper that records its argv into the same `argv.log`."""
        self._write("python-wrapper", LOGGING_PYTHON_STUB)
        return str(self.dir / "python-wrapper")

    @property
    def argv(self) -> str:
        log = self.dir / "argv.log"
        return log.read_text() if log.exists() else ""

    @property
    def systemctl_calls(self) -> list[str]:
        log = self.dir / "systemctl.log"
        return log.read_text().splitlines() if log.exists() else []

    def set_parameters(self, spec: dict) -> None:
        (self.dir / "params.json").write_text(json.dumps(spec))

    def fail_systemctl(self, **codes: int) -> None:
        (self.dir / "codes.json").write_text(json.dumps(codes))


def stubs(tmp_path, monkeypatch, parameters=None, **codes) -> Stubs:
    """A `Stubs` whose directory is first on `PATH` for the rest of the test.

    `parameters` maps a full SSM parameter name to `{"value": ...}` or
    `{"error": "<AWS error code>"}`; a name that is absent answers `ParameterNotFound`,
    which is what the real CLI does. `PATH` is set through `monkeypatch` so one case
    cannot leave a stub in front of the next one's `aws`.
    """
    directory = tmp_path / "stub-bin"
    directory.mkdir(exist_ok=True)
    made = Stubs(directory)
    made._write("aws", AWS_STUB)
    made._write("systemctl", SYSTEMCTL_STUB)
    made.set_parameters(parameters if parameters is not None else
                        {name: {"value": value} for name, value in VALID.items()})
    made.fail_systemctl(**codes)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return made


def runtime_stub(tmp_path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body.format(python=sys.executable))
    path.chmod(0o755)
    return str(path)


def serve_script(tmp_path, image="vllm/vllm-openai:nightly", extra="") -> pathlib.Path:
    """A stand-in for `models/marlin2b/serve.sh` with the flags a case needs.

    Generated rather than copied so a case can add `--reasoning-parser` without
    editing a file another track owns; `test_prereqs.py` checks the real script too.
    """
    path = tmp_path / "serve.sh"
    path.write_text(SERVE_SCRIPT.format(image=image, extra=extra))
    return path


def config(tmp_path, mode="dev", previous="PREVIOUS=1\n", **overrides):
    """A `Config` pointed at a temporary `/etc`, with the previous env file in place.

    `previous=None` is a first install (no file yet). The owner is the current user,
    so `shutil.chown` succeeds without root; production passes `ubuntu`.
    """
    etc = tmp_path / "etc"
    etc.mkdir(exist_ok=True)
    env_file = etc / "marlin2b-gateway.env"
    if previous is not None:
        env_file.write_text(previous)
    settings = dict(mode=mode, env_file=env_file, owner=owner_name(),
                    runtime_python=sys.executable,
                    units=("marlin2b-vllm", "marlin2b-gateway"))
    settings.update(overrides)
    # Only generate the default script when the case did not bring its own: both land
    # at `tmp_path/serve.sh`, so generating it unconditionally would overwrite the
    # case's version with the default one.
    if "serve_script" not in settings:
        settings["serve_script"] = serve_script(tmp_path)
    return preflight.Config(**settings)


def owner_name() -> str:
    """The current user, i.e. an owner `shutil.chown` can actually set without root."""
    return getpass.getuser()


def run_installer(cfg_args: list[str], env=None) -> subprocess.CompletedProcess:
    """`preflight.py` as a subprocess, for the cases that need a process to die."""
    return subprocess.run([sys.executable, str(API_DIR / "deploy" / "preflight.py"),
                           *cfg_args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, cwd=str(API_DIR))
