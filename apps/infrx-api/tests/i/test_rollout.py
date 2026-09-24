#!/usr/bin/env python3
"""I2B.c: the rollout runbook's scripts (`infra/rollout/`), checked without running them
against anything: they are strict bash, carry no secret, travel through SSM byte for
byte, and order the revert so the restored runtime starts on its own code.

`ssm.sh` runs against a fake `aws`; the box steps are only parsed. The coordinator runs
them for real (research/plan/evidence/i/I2B-*.md, handback).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

from . import support

ROLLOUT = support.REPO / "infra" / "rollout"
STEPS = sorted((ROLLOUT / "steps").glob("*.sh"))
SECRET_SHAPES = (r"postgres(?:ql)?://[^:@\s/]+:[^@\s]+@", r"eyJ[A-Za-z0-9_-]{16,}",
                 r"AKIA[0-9A-Z]{16}", r"--value\s+(?!file://)\S",
                 r"-e\s+[A-Z_]+=\S",         # docker -e passes names; a value there is inline
                 # a credential-named variable assigned a literal: the runbook reads those
                 # with `read -rs` or `$(aws ssm ...)`, never writes the value
                 r"""\b[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|DSN)=[^$\s"'<`…]""")

FAKE_AWS = '''#!{python}
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "aws.log").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if "send-command" in args:
    print("cmd-1")
elif "Status" in args:
    print((here / "status").read_text())
else:
    print("step output")
'''


def test_backend_deploy__every_rollout_step_is_strict_bash_that_names_no_secret():
    """Each script parses, stops at the first failure, refuses to run without the release
    it acts on - the cutover also without the migration digest step 6 applied - and
    contains nothing shaped like a credential or an inline parameter value: secrets are
    read on the box by preflight.py, from SSM, and never travel."""
    assert [p.name for p in STEPS] == ["10-inventory.sh", "20-prepull.sh", "25-save-edge.sh",
                                       "30-pause.sh", "40-checkout.sh", "45-s3-check.sh",
                                       "50-install.sh",
                                       "60-verify-local.sh", "71-pool-budget.sh",
                                       "72-observe-install.sh", "73-observe-status.sh",
                                       "90-revert.sh", "91-abort.sh",
                                       "93-restore-edge.sh", "95-maintenance.sh"]
    for path in [*STEPS, ROLLOUT / "ssm.sh", ROLLOUT / "verify-external.sh"]:
        text = path.read_text()
        done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, (path.name, done.stderr)
        if path.parent.name == "steps":
            assert "set -euo pipefail" in text, path.name
            if "$RELEASE" in text:
                assert ': "${RELEASE:?' in text, f"{path.name} runs without a release"
        for shape in SECRET_SHAPES:
            assert not re.search(shape, text), (path.name, shape)
    assert ': "${MIGRATION_DIGEST:?' in (ROLLOUT / "steps" / "50-install.sh").read_text()
    readme = (ROLLOUT / "README.md").read_text()
    for shape in SECRET_SHAPES:
        assert not re.search(shape, readme), shape


def _ssm(tmp_path, *args, status="Success"):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "aws").write_text(FAKE_AWS.format(python=sys.executable))
    (stub / "aws").chmod(0o755)
    (stub / "status").write_text(status)
    (stub / "aws.log").unlink(missing_ok=True)
    done = subprocess.run(["bash", str(ROLLOUT / "ssm.sh"), *args], capture_output=True,
                          text=True, env={**os.environ, "POLL_S": "0",
                                          "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
    log = stub / "aws.log"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return done, calls


def test_backend_deploy__ssm_carries_a_step_byte_for_byte(tmp_path):
    """What runs on the box is exactly the step, preceded by one `export` per argument:
    the base64 in the send-command decodes to those bytes; the document is
    AWS-RunShellScript on the pilot instance; the exit status is the invocation's."""
    step = ROLLOUT / "steps" / "50-install.sh"
    done, calls = _ssm(tmp_path, str(step), "RELEASE=abc123")
    assert done.returncode == 0, done.stderr
    send = next(call for call in calls if "send-command" in call)
    assert send[send.index("--instance-ids") + 1] == "i-0e8449a4ffca29bab"
    assert send[send.index("--document-name") + 1] == "AWS-RunShellScript"
    command = json.loads(send[send.index("--parameters") + 1])["commands"][0]
    wrapped = re.fullmatch(r"echo (\S+) \| base64 -d > /root/infrx-step.sh && .*", command)
    assert wrapped, command
    assert base64.b64decode(wrapped.group(1)) == b"export RELEASE=abc123\n" + step.read_bytes()

    done, _ = _ssm(tmp_path, str(step), "RELEASE=abc123", status="Failed")
    assert done.returncode != 0
    done, calls = _ssm(tmp_path, str(step), "release abc123")
    assert done.returncode == 2 and calls == []


def test_ops_recover__the_revert_restores_the_tree_before_the_runtime():
    """The monolith and the engine unit run from the working tree, so the revert checks
    out the previous HEAD before rollback.sh restarts the restored engine and gateway, and
    reopens the edge after; the pause saves that HEAD before it replaces the edge."""
    revert = (ROLLOUT / "steps" / "90-revert.sh").read_text()
    order = [revert.find(s) for s in ("checkout --quiet --detach \"$previous\"",
                                      'ENGINE=restart "$d/rollback.sh" "$BACKUP"',
                                      '"$d/drain.sh" resume')]
    assert -1 not in order and order == sorted(order), order
    pause = (ROLLOUT / "steps" / "30-pause.sh").read_text()
    order = [pause.find(s) for s in ("pre-$RELEASE.head", 'edge_install "$d"',
                                     '"$d/drain.sh" pause')]
    assert -1 not in order and order == sorted(order), order


def test_backend_deploy__the_cutover_keeps_the_engines_concurrency(tmp_path):
    """The box's engine serves 32 sequences (its unit passes `--max-num-seqs 32`); the
    release's serve.sh reads `ENGINE_MAX_NUM_SEQS` from the validated file and defaults
    to 8, so the cutover step hands install.sh 32 as a schema setting unless the operator
    names another value - a silent 4x cut otherwise. Runs the step's own bytes against a
    stand-in checkout; without step 6's digest (64 hex, or exactly `nothing-pending`) it
    does not reach install.sh at all."""
    box = tmp_path / "box"
    deploy = box / "apps" / "infrx-api" / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "install.sh").write_text('#!/usr/bin/env bash\n'
                                       'echo "install INFRX_SET=[$INFRX_SET] $INFRX_MODE $ENGINE"\n')
    (deploy / "install.sh").chmod(0o755)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "git").write_text("#!/usr/bin/env bash\necho abc123\n")
    (stub / "git").chmod(0o755)
    step = (ROLLOUT / "steps" / "50-install.sh").read_text().replace(
        "/home/ubuntu/model-inference", str(box))

    def cutover(**env):
        return subprocess.run(["bash", "-c", step], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                                   "RELEASE": "abc123", **env})
    done = cutover(MIGRATION_DIGEST="nothing-pending")
    assert done.returncode == 0, done.stderr
    assert "install INFRX_SET=[ENGINE_MAX_NUM_SEQS=32 ] pilot restart" in done.stdout
    done = cutover(MIGRATION_DIGEST="nothing-pending", ENGINE_MAX_NUM_SEQS="16")
    assert "INFRX_SET=[ENGINE_MAX_NUM_SEQS=16 ]" in done.stdout
    done = cutover(MIGRATION_DIGEST="a" * 64)
    assert done.returncode == 0 and "install INFRX_SET=" in done.stdout
    for statement in (None, "nothing-pendng", "x", "A" * 64, "a" * 63, "a" * 65,
                      "nothing-pending-x"):
        done = cutover(**({} if statement is None else {"MIGRATION_DIGEST": statement}))
        assert done.returncode != 0 and "install" not in done.stdout, statement


FAKE_CURL = """#!{python}
import pathlib, stat, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "curl.log").open("a") as log:
    log.write(repr(args) + "\\n")
    for i, arg in enumerate(args[:-1]):
        if arg == "-H" and args[i + 1].startswith("@"):
            path = pathlib.Path(args[i + 1][1:])
            log.write("HEADER MODE " + oct(stat.S_IMODE(path.stat().st_mode)) + " " + str(path)
                      + "\\n")
            log.write("HEADER FILE " + path.read_text())
    if "-o" in args:
        log.write("BODY " + args[args.index("-o") + 1] + "\\n")
if "-o" in args and args[args.index("-o") + 1] != "/dev/null":
    pathlib.Path(args[args.index("-o") + 1]).write_text('{{"ok":true}}')
if "-D" in args:
    print("server-timing: total;dur=1")
if "-w" in args:
    print("200", end="")
"""


def test_backend_deploy__verify_external_never_puts_a_key_on_a_command_line(tmp_path):
    """Every key the external check uses reaches curl in a header file, so none is ever an
    argument (visible in `ps` and /proc on the coordinator host) or printed; the key is
    still what curl sends. That file is 0600 while it exists, and it - like the response
    body file, which is never a fixed /tmp path - is a fresh temporary file, gone when the
    script exits."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(FAKE_CURL.format(python=sys.executable))
    (stub / "curl").chmod(0o755)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    keys = {name: f"{support.MARKER}-{name.lower()}"
            for name in ("INFRX_TEST_KEY", "INFRX_REVOKED_KEY", "LEGACY_KEY")}
    done = subprocess.run(["bash", str(ROLLOUT / "verify-external.sh")], capture_output=True,
                          text=True, env={**os.environ, **keys, "TMPDIR": str(scratch),
                                          "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
    log = (stub / "curl.log").read_text()
    argv = [line for line in log.splitlines() if not line.startswith(("HEADER ", "BODY "))]
    assert argv and not [line for line in argv if support.MARKER in line]
    assert support.MARKER not in done.stdout + done.stderr
    for key in keys.values():
        assert f"HEADER FILE Authorization: Bearer {key}\n" in log, key
    modes = [line.split(" ", 3)[2:] for line in log.splitlines() if line.startswith("HEADER MODE")]
    assert modes and all(mode == "0o600" and path.startswith(str(scratch)) for mode, path in modes)
    bodies = [line[5:] for line in log.splitlines()
              if line.startswith("BODY ") and line != "BODY /dev/null"]
    assert bodies and all(body.startswith(f"{scratch}/") for body in bodies), bodies
    assert list(scratch.iterdir()) == [], "a temporary file outlived the script"


BOX_BINARIES = ("hostname", "uptime", "df", "docker", "git", "systemctl", "ss", "curl")


def test_backend_deploy__the_read_only_steps_print_names_never_values(tmp_path):
    """10-inventory and 60-verify-local are the steps that read the env file, and their
    output lands in SSM and the coordinator's record: run with a canary env file (every
    box binary a silent success), each prints the file's names and never a value."""
    canary = f"{support.MARKER}-canary"
    env_file = tmp_path / "marlin2b-gateway.env"
    env_file.write_text(f"INFRX_MODE=pilot\nDATABASE_URL=postgresql://infrx:{canary}@db/x\n"
                        f"SUPABASE_SERVICE_ROLE_KEY={canary}\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    for name in BOX_BINARIES:
        (stub / name).write_text("#!/usr/bin/env bash\nexit 0\n")
        (stub / name).chmod(0o755)
    for step in ("10-inventory.sh", "60-verify-local.sh"):
        text = (ROLLOUT / "steps" / step).read_text().replace("/etc/marlin2b-gateway.env",
                                                              str(env_file))
        done = subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
        assert done.returncode == 0, (step, done.stderr)
        assert "DATABASE_URL" in done.stdout and "SUPABASE_SERVICE_ROLE_KEY" in done.stdout, step
        assert support.MARKER not in done.stdout + done.stderr, step


FAKE_DOCKER = """#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
with (here / "docker.log").open("a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "env": {{k: os.environ.get(k) for k in (
        "INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET")}}}}) + "\\n")
sys.exit(1 if sys.argv[1] == os.environ.get("DOCKER_FAIL") else 0)
"""


def _box_stubs(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(FAKE_DOCKER.format(python=sys.executable))
    (stub / "git").write_text('#!/usr/bin/env bash\necho "$HEAD_SHA"\n')
    for name in ("docker", "git"):
        (stub / name).chmod(0o755)
    return stub


def _docker_calls(stub):
    log = stub / "docker.log"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    log.unlink(missing_ok=True)
    return calls


def test_ops_recover__the_saved_edge_comes_back_byte_for_byte(tmp_path):
    """25-save-edge keeps the live Caddyfile and prints its sha256; after the window put
    the maintenance site there, 93-restore-edge writes it back in place (the same inode:
    Caddy's single-file bind mount keeps seeing it) and reloads through the admin socket.
    A saved file that does not match the recorded sha256 restores nothing."""
    stub = _box_stubs(tmp_path)
    live = tmp_path / "Caddyfile"
    live.write_bytes(b"{\n\tadmin localhost:2019\n}\n:443 { reverse_proxy 127.0.0.1:8001 }\n")
    original = live.read_bytes()
    mounted = tmp_path / "mounted"          # what Caddy's single-file bind mount holds: the inode
    mounted.hardlink_to(live)
    logs = tmp_path / "w4-logs"

    def step(name, **env):
        text = (ROLLOUT / "steps" / name).read_text()
        text = (text.replace("--config /etc/caddy/Caddyfile", "--config @IN-CONTAINER@")
                .replace("/etc/caddy/Caddyfile", str(live))
                .replace("@IN-CONTAINER@", "/etc/caddy/Caddyfile")
                .replace("/opt/dlami/nvme/w4-logs", str(logs)))
        return subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", **env})

    saved = step("25-save-edge.sh")
    assert saved.returncode == 0, saved.stderr
    printed = re.search(r"^saved=(\S+)$", saved.stdout, re.M)
    assert printed, saved.stdout
    path = printed.group(1)
    assert re.fullmatch(rf"{logs}/Caddyfile\.live-\d{{8}}T\d{{6}}Z", path), path
    sha = hashlib.sha256(original).hexdigest()
    assert f"{sha}  {path}\n" in saved.stdout, "the saved edge's sha256 was not printed"
    assert pathlib.Path(path).read_bytes() == original
    assert [c["argv"][0] for c in _docker_calls(stub)] == ["inspect"]   # read-only

    live.write_bytes(b"# maintenance\n:443 { respond 503 }\n")          # the window's edge
    wrong = step("93-restore-edge.sh", SAVED=path, SAVED_SHA256="0" * 64)
    assert wrong.returncode != 0 and live.read_bytes().startswith(b"# maintenance")
    assert _docker_calls(stub) == [], "reloaded an edge it did not restore"
    for missing in ({"SAVED": path}, {"SAVED_SHA256": sha}):
        assert step("93-restore-edge.sh", **missing).returncode != 0
    assert live.read_bytes().startswith(b"# maintenance")

    restored = step("93-restore-edge.sh", SAVED=path, SAVED_SHA256=sha)
    assert restored.returncode == 0, restored.stderr
    assert live.read_bytes() == original and mounted.read_bytes() == original
    assert [c["argv"] for c in _docker_calls(stub)] == [
        ["exec", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile",
         "--adapter", "caddyfile", "--address", "unix//config/admin.sock"]]


def test_backend_deploy__the_real_bucket_check_runs_the_release_image_before_the_install(
        tmp_path):
    """45-s3-check runs M1-L2's conformance in the image built from the checked-out
    release, against AWS S3 and the media bucket, with the instance role: only the two
    names travel into the container (never INFRX_M_S3_LOCAL_CREDS). It refuses a checkout
    that is not the release, and a red run is a failed step."""
    stub = _box_stubs(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    release = "c" * 40
    text = (ROLLOUT / "steps" / "45-s3-check.sh").read_text().replace(
        "/home/ubuntu/model-inference", str(box))

    def check(**env):
        return subprocess.run(["bash", "-c", text], capture_output=True, text=True,
                              env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                                   "HEAD_SHA": release, "INFRX_M_S3_LOCAL_CREDS": "1", **env})

    assert check().returncode != 0 and _docker_calls(stub) == []
    stale = check(RELEASE=release, HEAD_SHA="d" * 40)
    assert stale.returncode == 2 and "40-checkout" in stale.stderr
    assert _docker_calls(stub) == []

    done = check(RELEASE=release)
    assert done.returncode == 0, done.stderr
    assert f"real-bucket conformance passed for {release}" in done.stdout
    build, run = _docker_calls(stub)
    assert build["argv"][0] == "build" and build["argv"][-3:] == [
        "-t", f"infrx-runtime:{release}", "apps/infrx-api"]
    argv = run["argv"]
    assert argv[0] == "run" and f"infrx-runtime:{release}" in argv
    assert f"{box}:/repo:ro" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "-e"] == [
        "INFRX_M_S3_ENDPOINT", "INFRX_M_S3_BUCKET"]
    assert run["env"]["INFRX_M_S3_ENDPOINT"] == "https://s3.us-east-1.amazonaws.com"
    assert run["env"]["INFRX_M_S3_BUCKET"] == "llm-bootcamp-641134885443"
    # the container's exit status is pytest's: the script ends with it, nothing after
    assert "--require-hashes" in argv[-1] and argv[-1].rstrip().endswith(" tests/m/test_s3.py")

    check(RELEASE=release, S3_MEDIA_BUCKET="another-bucket")
    assert _docker_calls(stub)[-1]["env"]["INFRX_M_S3_BUCKET"] == "another-bucket"
    red = check(RELEASE=release, DOCKER_FAIL="run")
    assert red.returncode != 0 and "passed" not in red.stdout
