"""W6 infra-libs: the coordinator-host lib (`infra/rollout/host-lib.sh`) and the box lib
(`infra/rollout/box-lib.sh`) as the scripts that source them use them, run against recording
stubs on PATH (the test_ops_steps pattern). Nothing touches AWS, SSM, the box or hosted Supabase.

Every case asserts on what the helper DID (the stub's argv and environment, files written,
the exit code) and that no secret value reached an argument or the output.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from . import support

ROLLOUT = support.REPO / "infra" / "rollout"
HOST_LIB = ROLLOUT / "host-lib.sh"
BOX_LIB = ROLLOUT / "box-lib.sh"
SECRET = f"{support.MARKER}-ssm-value"
#: The coordinator-host scripts this lane owns that read AWS: each sources the one lib.
HOST_SCRIPTS = ("ssm.sh", "hosted-migrate.sh", "operator-cli.sh", "unblock-coordinator.sh",
                "../lab/rollout/lab-release.sh")   # WR-IL-1

AWS = '''#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
with (here / "calls.log").open("a") as log:
    log.write(json.dumps({{"tool": pathlib.Path(sys.argv[0]).name, "argv": sys.argv[1:],
                          "aws_env": sorted(k for k in os.environ if k.startswith("AWS_"))}}) + "\\n")
values = json.loads((here / "ssm.json").read_text()) if (here / "ssm.json").exists() else {{}}
args = sys.argv[1:]
name = args[args.index("--name") + 1] if "--name" in args else None
if name in values:
    sys.stdout.write(values[name] + "\\n")
sys.exit(int(os.environ.get("STUB_EXIT_" + pathlib.Path(sys.argv[0]).name, "0")))
'''


def stub(tmp_path: Path, values: dict[str, str] | None = None, *tools: str) -> Path:
    bin_ = tmp_path / "bin"
    bin_.mkdir(parents=True, exist_ok=True)
    for tool in ("aws", *tools):
        (bin_ / tool).write_text(AWS.format(python=sys.executable))
        (bin_ / tool).chmod(0o755)
    (bin_ / "ssm.json").write_text(json.dumps(values or {}))
    return bin_


def calls(bin_: Path) -> list[dict]:
    log = bin_ / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def host(script: str, bin_: Path, cwd: Path | None = None, **env) -> subprocess.CompletedProcess:
    """`script` run by bash after sourcing host-lib.sh, the stale AWS_* exports present."""
    return subprocess.run(["bash", "-c", f'set -euo pipefail\n. "{HOST_LIB}"\n{script}'],
                          capture_output=True, text=True, cwd=cwd, env={
                              "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
                              "AWS_ACCESS_KEY_ID": "stale", "AWS_SECRET_ACCESS_KEY": "stale",
                              "AWS_SESSION_TOKEN": "stale", **env})


# --- INFRA-05: the coordinator-host lib --------------------------------------------------------
def test_rollout_host__aws_drops_the_stale_keys_and_pins_the_region(tmp_path):
    """aws(): CLAUDE.md "AWS access from this host" - the exported keys fail with
    InvalidClientTokenId, so every call runs without the three AWS_* names, in us-east-1
    unless REGION says otherwise (ssm.sh's input)."""
    bin_ = stub(tmp_path)
    assert host("aws sts get-caller-identity", bin_).returncode == 0
    assert host("REGION=us-west-2 aws sts get-caller-identity", bin_).returncode == 0
    first, second = calls(bin_)
    assert first["aws_env"] == [] and second["aws_env"] == []
    assert first["argv"] == ["--region", "us-east-1", "sts", "get-caller-identity"]
    assert second["argv"][:2] == ["--region", "us-west-2"]


def test_rollout_host__ssm_to_file_is_0600_and_refuses_an_empty_value(tmp_path):
    """ssm_value NAME prints the value (for `VAR=$(ssm_value …)`); ssm_to_file NAME FILE
    writes it into a 0600 file and prints nothing; an empty or unreadable parameter is a
    failure that leaves no file (never a blank secret downstream)."""
    bin_ = stub(tmp_path, {"/p/full": SECRET, "/p/empty": ""})
    out = tmp_path / "out"
    done = host(f'ssm_to_file /p/full "{out}"', bin_)
    assert done.returncode == 0 and SECRET not in done.stdout + done.stderr, done.stderr
    assert out.read_text().strip() == SECRET and stat.S_IMODE(out.stat().st_mode) == 0o600
    assert calls(bin_)[0]["argv"] == ["--region", "us-east-1", "ssm", "get-parameter",
                                      "--with-decryption", "--name", "/p/full", "--query",
                                      "Parameter.Value", "--output", "text"]
    for name, env in (("/p/empty", {}), ("/p/full", {"STUB_EXIT_aws": "254"})):
        target = tmp_path / f"refused-{len(env)}"
        done = host(f'ssm_to_file {name} "{target}"', bin_, **env)
        assert done.returncode != 0 and not target.exists(), (name, done.stderr)
        assert name in done.stderr and SECRET not in done.stdout + done.stderr
    done = host("v=$(ssm_value /p/full); [ \"$v\" = '%s' ]" % SECRET, bin_)
    assert done.returncode == 0, done.stderr


def test_rollout_host__need_venv_refuses_outside_the_repo_root(tmp_path):
    """need_venv: the host scripts run from the repo root after `make api-env`; elsewhere
    they stop (exit 2) naming that, before any AWS call; there PY is the pinned interpreter."""
    bin_ = stub(tmp_path)
    done = host("need_venv; echo ran", bin_, cwd=tmp_path)
    assert done.returncode == 2 and "make api-env" in done.stderr and "ran" not in done.stdout
    venv = tmp_path / "apps/infrx-api/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n")
    (venv / "python").chmod(0o755)
    done = host('need_venv; echo "PY=$PY"', bin_, cwd=tmp_path)
    assert done.returncode == 0 and done.stdout.strip() == "PY=apps/infrx-api/.venv/bin/python"


def test_rollout_host__say_stamps_and_appends_to_the_host_log(tmp_path):
    """say: one UTC-stamped line on stdout and appended to HOST_LOG (hosted-migrate.sh's run
    log); without HOST_LOG it only prints."""
    bin_ = stub(tmp_path)
    log = tmp_path / "run.log"
    done = host(f'HOST_LOG="{log}"; say one; say two; HOST_LOG=; say three', bin_)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == ["one", "two", "three"]
    assert all(line[:4].isdigit() and line[19] == "Z" for line in lines), lines
    assert log.exists() and log.read_text().splitlines() == lines[:2]


def test_rollout_host__every_host_script_sources_the_one_lib():
    """INFRA-05: one aws()/say()/SSM-read implementation. Each owned host script parses and
    sources host-lib.sh from its own directory, and defines no aws() of its own; the libs
    parse and are sourced, never run (no shebang, no `set -e` of their own)."""
    for lib in (HOST_LIB, BOX_LIB):
        assert subprocess.run(["bash", "-n", str(lib)]).returncode == 0, lib.name
        assert not lib.read_text().startswith("#!"), lib.name
    for name in HOST_SCRIPTS:
        text = (ROLLOUT / name).read_text()
        assert subprocess.run(["bash", "-n", str(ROLLOUT / name)]).returncode == 0, name
        lib = os.path.relpath(HOST_LIB, (ROLLOUT / name).parent)
        assert f'. "$(dirname "${{BASH_SOURCE[0]}}")/{lib}"' in text, name
        assert "aws() {" not in text and "aws --region" not in text, name


def test_rollout_host__operator_cli_reads_its_two_secrets_by_name_into_the_environment(tmp_path):
    """operator-cli.sh: OPERATIONS_DATABASE_URL and INFRX_OPERATOR_KEY are read from SSM by
    NAME into the CLI's environment (never argv, never printed); an empty one is exit 3 and
    the CLI never starts."""
    bin_ = stub(tmp_path, {"/model-inference/pg_journal_url": "postgresql://o:" + SECRET + "@h/d",
                           "/model-inference/operator_key": SECRET})
    venv = tmp_path / "apps/infrx-api/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "json.dump({'argv': sys.argv[1:], 'dsn': os.environ.get('OPERATIONS_DATABASE_URL'),"
        " 'key': os.environ.get('INFRX_OPERATOR_KEY'), 'path': os.environ.get('PYTHONPATH')},"
        f" open({str(tmp_path / 'cli.json')!r}, 'w'))\n")
    (venv / "python").chmod(0o755)
    script = str(ROLLOUT / "operator-cli.sh")
    env = {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "AWS_ACCESS_KEY_ID": "stale"}
    done = subprocess.run(["bash", script, "flag", "--name", "signup_grant", "--dry-run"],
                          capture_output=True, text=True, cwd=tmp_path, env=env)
    assert done.returncode == 0, done.stderr
    assert SECRET not in done.stdout + done.stderr
    cli = json.loads((tmp_path / "cli.json").read_text())
    assert cli == {"argv": ["-m", "infrx.operations.cli", "flag", "--name", "signup_grant", "--dry-run"],
                   "dsn": "postgresql://o:" + SECRET + "@h/d", "key": SECRET, "path": "apps/infrx-api"}
    assert [c["argv"][c["argv"].index("--name") + 1] for c in calls(bin_)] == [
        "/model-inference/pg_journal_url", "/model-inference/operator_key"]
    assert all(c["aws_env"] == [] for c in calls(bin_))
    (tmp_path / "cli.json").unlink()
    (bin_ / "ssm.json").write_text(json.dumps({"/model-inference/pg_journal_url": "postgresql://o@h/d",
                                               "/model-inference/operator_key": ""}))
    done = subprocess.run(["bash", script, "flag"], capture_output=True, text=True, cwd=tmp_path, env=env)
    assert done.returncode == 3 and "operator_key" in done.stderr
    assert not (tmp_path / "cli.json").exists()


def scratch_repo(path: Path) -> str:
    """A one-commit git checkout to run a host script from (its HEAD returned)."""
    path.mkdir(parents=True)
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@x"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "c"], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_rollout_host__hosted_migrate_says_into_its_run_log(tmp_path):
    """F2: hosted-migrate.sh hands host-lib's say its run log (HOST_LOG=$LOG): the lines it
    prints before a stop are also in $BACKUP_ROOT/migrate-<stamp>.log, the window's record."""
    bin_ = stub(tmp_path, None, "docker")
    repo = tmp_path / "repo"
    release = scratch_repo(repo)
    venv = repo / "apps/infrx-api/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n")
    (venv / "python").chmod(0o755)
    backups = tmp_path / "backups"
    done = subprocess.run(["bash", str(ROLLOUT / "hosted-migrate.sh"), "--release", release],
                          capture_output=True, text=True, cwd=repo, env={
                              "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path),
                              "BACKUP_ROOT": str(backups), "STUB_EXIT_docker": "1"})
    assert done.returncode == 3 and "ss or docker unavailable" in done.stdout, done.stdout + done.stderr
    logs = list(backups.glob("migrate-*.log"))
    assert len(logs) == 1 and logs[0].read_text().splitlines() == done.stdout.splitlines(), done.stdout


def test_rollout_host__unblock_coordinator_runs_aws_in_aws_region(tmp_path):
    """F4: unblock-coordinator.sh maps AWS_REGION onto host-lib's REGION: every aws call names
    --region, us-east-1 unless AWS_REGION says otherwise (the first call, sts, stops the run
    here: the stub fails it)."""
    bin_ = stub(tmp_path)
    repo = tmp_path / "repo"
    scratch_repo(repo)
    for extra, region in (({}, "us-east-1"), ({"AWS_REGION": "eu-west-1"}, "eu-west-1")):
        done = subprocess.run(["bash", str(ROLLOUT / "unblock-coordinator.sh")], capture_output=True,
                              text=True, cwd=repo, env={
                                  "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path),
                                  "AWS_ACCESS_KEY_ID": "stale", "STUB_EXIT_aws": "254", **extra})
        assert done.returncode == 254, done.stderr
        last = calls(bin_)[-1]
        assert last["argv"][:3] == ["--region", region, "sts"] and "AWS_ACCESS_KEY_ID" not in last["aws_env"], last


# --- INFRA-06: the box lib, as 72-observe-install.sh uses it -------------------------------------
INSTALL = ('#!/usr/bin/env bash\n'
           'if [ "$1" = -d ]; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi\n')


def test_rollout_host__observe_install_refuses_an_unreadable_or_empty_secret(tmp_path):
    """72-observe-install.sh writes its env files through box-lib's stage_env/place: an SSM
    read that fails, or answers empty, stops the step (exit 2) naming the parameter, with no
    env file, no staged copy and no unit enabled. Oracle: the old write_env wrote
    `INFRX_CANARY_KEY=` and went on (a failed command substitution inside printf's argument
    does not trip set -e), so the canary ran keyless. A checkout without the lib is BLOCKED
    (exit 3) naming it."""
    for case, answer, env in (("empty", "", {}), ("failed", "partial", {"STUB_EXIT_aws": "254"})):
        bin_ = stub(tmp_path / case, {"/model-inference/canary_key": answer}, "systemctl")
        (bin_ / "git").write_text("#!/bin/sh\necho " + "c" * 40 + "\n")
        (bin_ / "install").write_text(INSTALL)
        for tool in ("git", "install"):
            (bin_ / tool).chmod(0o755)
        root = tmp_path / case / "root"
        (root / "etc" / "systemd" / "system").mkdir(parents=True)
        clip = tmp_path / case / "clip.mp4"
        clip.write_bytes(b"x")
        done = subprocess.run(
            ["bash", str(ROLLOUT / "steps" / "72-observe-install.sh")], capture_output=True, text=True,
            env={"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "REPO": str(support.REPO),
                 "INFRX_ROOT": str(root), "RELEASE": "c" * 40, "CANARY_VIDEO": str(clip),
                 "CANARY_KEY_PARAM": "/model-inference/canary_key", **env})
        assert done.returncode == 2 and "/model-inference/canary_key" in done.stderr, (case, done.stderr)
        assert sorted(p.name for p in (root / "etc").iterdir()) == ["systemd"], case
        assert [c for c in calls(bin_) if c["tool"] == "systemctl"] == [], case
        assert "partial" not in done.stdout + done.stderr, case
    # a checkout older than the lib (an I8+ release before W6): BLOCKED by name, nothing run
    old = tmp_path / "pre-w6"
    (old / "infra/observe/systemd").mkdir(parents=True)
    (old / "infra/alerts").mkdir(parents=True)
    (old / "infra/alerts/operations.json").write_text("{}")
    done = subprocess.run(
        ["bash", str(ROLLOUT / "steps" / "72-observe-install.sh")], capture_output=True, text=True,
        env={"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "REPO": str(old), "INFRX_ROOT": str(root),
             "RELEASE": "c" * 40, "CANARY_VIDEO": str(clip), "CANARY_KEY_PARAM": "/model-inference/canary_key"})
    assert done.returncode == 3 and "BLOCKED" in done.stderr and "box-lib.sh" in done.stderr, done.stderr
