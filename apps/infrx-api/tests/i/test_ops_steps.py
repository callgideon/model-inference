"""I8: the box steps and coordinator-host scripts of the continuous-operations lane, run
against stubs (PATH is the only seam: docker, curl, aws, systemctl, nvidia-smi, git). The
coordinator runs them for real; nothing here touches the box, AWS or hosted Supabase.

Every case asserts on what the script *did* (the stub's recorded argv/stdin, files written,
exit code) and that no secret value reached an argument or the output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import support

ROLLOUT = support.REPO / "infra" / "rollout"
STEPS = ROLLOUT / "steps"

RECORDING_STUB = '''#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with (here / "calls.log").open("a") as log:
    log.write(json.dumps({{"tool": pathlib.Path(sys.argv[0]).name, "argv": sys.argv[1:],
                          "stdin": stdin}}) + "\\n")
answer = here / (pathlib.Path(sys.argv[0]).name + ".out")
if answer.exists():
    sys.stdout.write(answer.read_text())
sys.exit(int(os.environ.get("STUB_EXIT_" + pathlib.Path(sys.argv[0]).name.replace("-", "_"), "0")))
'''


def stubs(tmp_path, *tools, outputs=None):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for tool in tools:
        (stub / tool).write_text(RECORDING_STUB.format(python=sys.executable))
        (stub / tool).chmod(0o755)
    for tool, text in (outputs or {}).items():
        (stub / f"{tool}.out").write_text(text)
    return stub


def calls(stub):
    log = stub / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def run_step(text: str, stub, env=None, stdin=None):
    return subprocess.run(["bash", "-c", text], capture_output=True, text=True, input=stdin,
                          env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                               **(env or {})})


def test_ops_continuous__the_pool_budget_step_hands_the_env_file_over_stdin(tmp_path):
    """71-pool-budget.sh: the installed env file reaches the budget script on stdin, inside
    the installed runtime image, with no network; no value is an argument; a proposed change
    travels as --set pairs."""
    canary = f"{support.MARKER}-dsn"
    env_file = tmp_path / "marlin2b-gateway.env"
    image = "sha256:" + "a" * 64
    env_file.write_text(f"INFRX_MODE=pilot\nINFRX_IMAGE={image}\n"
                        f"DATABASE_URL=postgresql://u:{canary}@h:5432/d\n")
    stub = stubs(tmp_path, "docker")
    text = (STEPS / "71-pool-budget.sh").read_text().replace("/etc/marlin2b-gateway.env",
                                                             str(env_file))
    done = run_step(text, stub, env={"SET": "DATABASE_POOL_MAX_SIZE=6"})
    assert done.returncode == 0, done.stderr
    (call,) = calls(stub)
    argv = call["argv"]
    assert argv[:5] == ["run", "--rm", "-i", "--network", "none"] and image in argv
    assert argv[-4:] == ["--units", "/repo/apps/infrx-api/deploy", "--set",
                         "DATABASE_POOL_MAX_SIZE=6"]
    assert canary in call["stdin"] and not any(canary in a for a in argv)
    assert support.MARKER not in done.stdout + done.stderr
    env_file.write_text("INFRX_MODE=pilot\n")                        # no image: refused
    assert run_step(text, stub).returncode == 2
