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


DOCKER_OBSERVE = '''#!{python}
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
record = {{"argv": args}}
if "--env-file" in args:
    record["env_file"] = args[args.index("--env-file") + 1]
    record["env_names"] = [l.split("=", 1)[0] for l in
                           pathlib.Path(record["env_file"]).read_text().splitlines()]
with (here / "docker.log").open("a") as log:
    log.write(json.dumps(record) + "\\n")
if "infrx.observe.alerts" in args:
    print(json.dumps({{"alert": "StuckHolds", "severity": "page", "value": 1, "labels": {{}},
                      "summary": "s", "runbook": "r"}}))
'''


def test_ops_continuous__one_observe_cycle_probes_exports_evaluates_and_delivers(tmp_path):
    """observe.sh: the host probe, then the durable exporter in the installed image with the
    DSN in a --env-file only (gone afterwards), then the evaluator over the merged rule set,
    then delivery - which, with no P-25 destination, is BLOCKED (exit 3), the alert kept."""
    canary = f"{support.MARKER}-dsn"
    env_file = tmp_path / "marlin2b-gateway.env"
    image = "sha256:" + "b" * 64
    env_file.write_text(f"INFRX_IMAGE={image}\nDATABASE_URL=postgresql://u:{canary}@h:5432/d\n"
                        f"SUPABASE_SERVICE_ROLE_KEY={canary}\n")
    stub = stubs(tmp_path, "nvidia-smi", "curl", "systemctl", "du", "install")
    (stub / "docker").write_text(DOCKER_OBSERVE.format(python=sys.executable))
    (stub / "docker").chmod(0o755)
    metrics, run = tmp_path / "metrics", tmp_path / "run"
    metrics.mkdir(), run.mkdir()
    done = run_step((support.REPO / "infra" / "observe" / "observe.sh").read_text(), stub, env={
        "REPO": str(support.REPO), "METRICS_DIR": str(metrics), "ENV_FILE": str(env_file),
        "RUNTIME_DIRECTORY": str(run), "HOME": str(tmp_path)})
    assert done.returncode == 3, (done.stdout, done.stderr)             # BLOCKED on P-25
    assert "BLOCKED" in done.stdout and canary not in done.stdout + done.stderr
    docker = [json.loads(line) for line in (stub / "docker.log").read_text().splitlines()]
    durable, evaluate = docker
    assert durable["env_names"] == ["DATABASE_URL"]                     # only the DSN crosses
    assert not any(canary in a for a in durable["argv"]) and image in durable["argv"]
    assert "--read-only" in durable["argv"] and "/observe/durable.py" in durable["argv"]
    assert "infrx.observe.alerts" in evaluate["argv"] and "/rules.json" in evaluate["argv"]
    assert list(run.iterdir()) == []                                    # DSN file removed
    assert "StuckHolds" in (metrics / "undelivered.jsonl").read_text()
    assert (metrics / "host.prom").exists()


def test_ops_continuous__installing_the_monitor_writes_env_files_from_ssm_by_name(tmp_path):
    """72-observe-install.sh: the four units from the release's deploy dir, the canary key
    and (when P-25 names one) the webhook read from SSM on the box into 0600 files - by
    parameter NAME on the command line, the value only in the file - then the timers."""
    secret = f"{support.MARKER}-ssm-value"
    stub = stubs(tmp_path, "aws", "systemctl", "git", outputs={"aws": secret + "\n"})
    (stub / "git.out").write_text("c" * 40 + "\n")
    (stub / "install").write_text("#!/usr/bin/env bash\n"
                                  'if [ "$1" = -d ]; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi\n')
    (stub / "install").chmod(0o755)
    root = tmp_path / "root"
    (root / "etc" / "systemd" / "system").mkdir(parents=True)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    env = {"RELEASE": "c" * 40, "CANARY_VIDEO": str(clip), "INFRX_ROOT": str(root),
           "REPO": str(support.REPO), "ALERT_WEBHOOK_PARAM": "/model-inference/alert_webhook",
           "ALERT_OWNER": "sofia"}
    done = run_step((STEPS / "72-observe-install.sh").read_text(), stub, env=env)
    assert done.returncode == 0, done.stderr
    assert secret not in done.stdout + done.stderr
    canary = root / "etc" / "infrx-canary.env"
    alert = root / "etc" / "infrx-alert.env"
    assert canary.read_text() == f"INFRX_CANARY_KEY={secret}\nCANARY_VIDEO={clip}\n"
    assert f"ALERT_WEBHOOK_URL={secret}\n" in alert.read_text() and "ALERT_OWNER=sofia\n" in alert.read_text()
    assert oct(canary.stat().st_mode & 0o777) == "0o600" and oct(alert.stat().st_mode & 0o777) == "0o600"
    assert not (root / "etc" / "infrx-observe.env").exists()            # no D10 monitor DSN yet
    assert sorted(p.name for p in (root / "etc" / "systemd" / "system").iterdir()) == [
        "infrx-canary.service", "infrx-canary.timer", "infrx-observe.service", "infrx-observe.timer"]
    ssm = [c["argv"] for c in calls(stub) if c["tool"] == "aws"]
    assert [a[a.index("--name") + 1] for a in ssm] == ["/model-inference/e4b_api_key",
                                                        "/model-inference/alert_webhook"]
    assert ["enable", "--now", "infrx-observe.timer", "infrx-canary.timer"] in \
        [c["argv"] for c in calls(stub) if c["tool"] == "systemctl"]
    # another checkout than RELEASE: refused before anything is written
    (stub / "git.out").write_text("d" * 40 + "\n")
    canary.unlink()
    assert run_step((STEPS / "72-observe-install.sh").read_text(), stub, env=env).returncode == 2
    assert not canary.exists()

